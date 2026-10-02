"""Live Chrome identity and MCP traffic for the operator desk.

The booth used to print the DevTools websocket. Operators need the browser,
the profile, and whether bytes are moving on the chrome-devtools-mcp link.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

_PROBE_JS = r"""
const endpoint = process.argv[2];
const pageUrl = process.argv[3] || "";
const emails = JSON.parse(process.argv[4] || "[]");
const pageTitle = process.argv[5] || "";

function strip(url) {
  const hash = url.indexOf("#");
  const bare = hash >= 0 ? url.slice(0, hash) : url;
  return bare.replace(/\/+$/, "");
}

const ws = new WebSocket(endpoint);
const pending = new Map();
let id = 0;

function call(method) {
  const msgId = ++id;
  ws.send(JSON.stringify({id: msgId, method}));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(msgId);
      reject(new Error("timeout " + method));
    }, 8000);
    pending.set(msgId, {
      resolve: (value) => { clearTimeout(timer); resolve(value); },
      reject: (error) => { clearTimeout(timer); reject(error); },
    });
  });
}

ws.addEventListener("message", (ev) => {
  const msg = JSON.parse(ev.data);
  const slot = pending.get(msg.id);
  if (!slot) return;
  pending.delete(msg.id);
  if (msg.error) slot.reject(new Error(msg.error.message || "cdp"));
  else slot.resolve(msg.result);
});

function finish(payload) {
  console.log(JSON.stringify(payload));
  try { ws.close(); } catch (e) {}
  process.exit(0);
}

ws.addEventListener("error", () => finish({ok: false}));
ws.addEventListener("open", async () => {
  try {
    const version = await call("Browser.getVersion");
    const targets = await call("Target.getTargets");
    let contexts = {};
    try { contexts = await call("Target.getBrowserContexts"); } catch (e) {}
    const want = strip(pageUrl);
    const infos = targets.targetInfos || [];
    let matched = null;
    if (want) {
      for (const target of infos) {
        if (target.type === "page" && strip(target.url || "").startsWith(want)) {
          matched = target;
          break;
        }
      }
    }
    if (!matched && pageTitle) {
      const needle = pageTitle.toLowerCase();
      for (const target of infos) {
        if (target.type !== "page") continue;
        const heading = (target.title || "").toLowerCase();
        if (heading && (heading.includes(needle) || needle.includes(heading))) {
          matched = target;
          break;
        }
      }
    }
    let contextId = matched ? (matched.browserContextId || "") : "";
    const defaultId = contexts.defaultBrowserContextId || "";
    const usedDefault = !contextId || contextId === defaultId;
    if (!contextId) contextId = defaultId;
    const blob = infos
      .filter((target) => target.type === "page" && (target.browserContextId || "") === contextId)
      .map((target) => (target.title || "") + " " + (target.url || ""))
      .join("\n")
      .toLowerCase();
    const seen = emails.filter((email) => email && blob.includes(String(email).toLowerCase()));
    finish({
      ok: true,
      product: version.product || "",
      userAgent: version.userAgent || "",
      emails: seen,
      usedDefault,
      pageUrl: matched ? (matched.url || "") : "",
      pageTitle: matched ? (matched.title || "") : "",
    });
  } catch (error) {
    finish({ok: false, error: String(error)});
  }
});
setTimeout(() => finish({ok: false, error: "timeout"}), 2500);
"""

_SAMPLER_PS1 = r"""
param([int]$Port = 9222)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class RhapsodeTcp {
  [DllImport("iphlpapi.dll")]
  public static extern uint GetExtendedTcpTable(IntPtr p, ref int len, bool sort, int af, int cls, uint rsv);
  public static int[] Clients(int port) {
    int size = 0;
    GetExtendedTcpTable(IntPtr.Zero, ref size, true, 2, 5, 0);
    IntPtr buf = Marshal.AllocHGlobal(size);
    try {
      if (GetExtendedTcpTable(buf, ref size, true, 2, 5, 0) != 0) return new int[0];
      int n = Marshal.ReadInt32(buf);
      var found = new System.Collections.Generic.List<int>();
      for (int i = 0; i < n; i++) {
        IntPtr row = buf + 4 + i * 24;
        if (Marshal.ReadInt32(row) != 5) continue;
        int raw = Marshal.ReadInt32(row, 16);
        int remote = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF);
        if (remote != port) continue;
        found.Add(Marshal.ReadInt32(row, 20));
      }
      return found.ToArray();
    } finally { Marshal.FreeHGlobal(buf); }
  }
}
"@
$stdout = [Console]::OpenStandardOutput()
$writer = New-Object System.IO.StreamWriter($stdout)
$writer.AutoFlush = $true
[Console]::SetOut($writer)
while ($true) {
  $read = [int64]0
  $write = [int64]0
  $clients = 0
  foreach ($procId in [RhapsodeTcp]::Clients($Port)) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
    if (-not $proc -or $proc.CommandLine -notlike "*chrome-devtools-mcp*") { continue }
    $row = Get-CimInstance -Query "SELECT IOReadBytesPersec, IOWriteBytesPersec FROM Win32_PerfRawData_PerfProc_Process WHERE IDProcess = $procId"
    if (-not $row) { continue }
    $read += [int64]$row.IOReadBytesPersec
    $write += [int64]$row.IOWriteBytesPersec
    $clients += 1
  }
  $writer.WriteLine((@{bytes_in=$read; bytes_out=$write; clients=$clients} | ConvertTo-Json -Compress))
  Start-Sleep -Seconds 1
}
"""


def parse_browser_endpoint(url: str) -> tuple[str, int, str] | None:
    """Return host, port, and browser id from a DevTools websocket URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {"ws", "wss", "http", "https"} or not parsed.hostname or not parsed.port:
        return None
    browser_id = parsed.path.rsplit("/", 1)[-1]
    return parsed.hostname, parsed.port, browser_id


def instance_name(product: str, user_agent: str) -> str:
    """Turn ``Chrome/154.0.8037.58`` into a label an operator can read."""
    name = (product or "Chrome").replace("/", " ").strip()
    if "Headless" in user_agent and not name.lower().startswith("headless"):
        name = "Headless " + name
    return name


def page_identity(url: str, title: str = "") -> tuple[str, str]:
    """Return the page address an operator reads, then the chapter title.

    The address keeps its host and path and drops only the scheme, so a card
    shows ``learning.oreilly.com/.../ch02.html`` with the chapter underneath.
    """
    raw = (url or "").strip()
    name = (title or "").strip()
    shown = raw.split("://", 1)[1] if "://" in raw else raw
    shown = shown.split("#", 1)[0]
    if shown and name and name not in {shown, raw}:
        return shown, name
    if shown:
        return shown, ""
    return name or "Unknown source", ""


def install_version(names: list[str]) -> str:
    """Pick the newest ``154.0.8037.58``-style directory name."""
    versions = [
        tuple(int(part) for part in name.split("."))
        for name in names
        if name and all(part.isdigit() for part in name.split("."))
    ]
    if not versions:
        return ""
    return ".".join(str(part) for part in max(versions))


def preferred_profile(profiles: list[dict], last_active: list[str]) -> dict | None:
    """Choose the profile Chrome most recently had in front."""
    by_dir = {profile.get("directory"): profile for profile in profiles}
    for directory in last_active:
        found = by_dir.get(directory)
        if found:
            return found
    return None


def profile_for_page(user_data: Path, profiles: list[dict], page_url: str) -> dict | None:
    """Choose the profile whose history last opened this page.

    The DevTools browser socket is usually already held by chrome-devtools-mcp,
    so a second connection never answers. The History database is the record
    of which signed-in profile actually has the chapter.
    """
    bare = (page_url or "").split("#", 1)[0].rstrip("/")
    if not bare.startswith("http"):
        return None
    best: dict | None = None
    best_time = -1
    for profile in profiles:
        database = user_data / str(profile.get("directory") or "") / "History"
        if not database.is_file():
            continue
        try:
            con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            row = con.execute(
                "select max(last_visit_time) from urls where url = ? or url like ?",
                (bare, bare + "%"),
            ).fetchone()
            con.close()
        except sqlite3.Error:
            continue
        stamp = int(row[0]) if row and row[0] is not None else -1
        if stamp > best_time:
            best_time = stamp
            best = profile
    return best


def match_profile(profiles: list[dict], emails: list[str], used_default: bool) -> dict | None:
    """Pick the Chrome profile that owns the page the booth is reading."""
    wanted = {email.lower() for email in emails if email}
    for profile in profiles:
        email = (profile.get("email") or "").lower()
        if email and email in wanted:
            return profile
    if used_default:
        for profile in profiles:
            if profile.get("directory") == "Default":
                return profile
    return None


def load_chrome_profiles(user_data: Path) -> list[dict]:
    """Read avatar names and signed-in emails from a Chrome user-data dir."""
    local_state = user_data / "Local State"
    if not local_state.is_file():
        return []
    try:
        cache = json.loads(local_state.read_text(encoding="utf-8")).get("profile", {}).get("info_cache", {})
    except (OSError, json.JSONDecodeError):
        return []
    profiles: list[dict] = []
    for directory, meta in cache.items():
        if not isinstance(meta, dict):
            continue
        email = ""
        preferences = user_data / directory / "Preferences"
        if preferences.is_file():
            try:
                accounts = json.loads(preferences.read_text(encoding="utf-8", errors="replace")).get("account_info") or []
            except (OSError, json.JSONDecodeError):
                accounts = []
            if accounts and isinstance(accounts[0], dict):
                email = accounts[0].get("email") or ""
        profiles.append({
            "directory": directory,
            "label": meta.get("name") or directory,
            "email": email,
        })
    return profiles


def blank_mcp(has_endpoint: bool) -> dict:
    """Status shown before the live probe answers, with no websocket URL."""
    if not has_endpoint:
        return {
            "connected": False,
            "active": False,
            "instance": "No Chrome connection",
            "profile": "The text came from a file.",
            "account": "",
            "bytes_in": 0,
            "bytes_out": 0,
        }
    return {
        "connected": False,
        "active": False,
        "instance": "Chrome",
        "profile": "Looking…",
        "account": "",
        "bytes_in": 0,
        "bytes_out": 0,
    }


def _win_to_wsl(path: str) -> Path | None:
    text = path.strip().strip('"')
    if len(text) < 3 or text[1] != ":":
        return None
    drive = text[0].lower()
    rest = text[2:].replace("\\", "/")
    return Path(f"/mnt/{drive}{rest}")


def _wsl_to_win(path: Path) -> str:
    text = path.as_posix()
    marker = "/mnt/"
    if text.startswith(marker) and len(text) > len(marker) + 1 and text[len(marker) + 1] == "/":
        drive = text[len(marker)].upper()
        rest = text[len(marker) + 2 :].replace("/", "\\")
        return f"{drive}:\\{rest}"
    return str(path)


def _local_appdata() -> Path | None:
    try:
        proc = subprocess.run(
            ["cmd.exe", "/c", "echo %LOCALAPPDATA%"],
            capture_output=True,
            text=True,
            cwd="/mnt/c/Windows",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in reversed(proc.stdout.splitlines()):
        found = _win_to_wsl(line)
        if found is not None:
            return found
    return None


def load_last_active(user_data: Path) -> list[str]:
    """Profile directories in most-recently-used order."""
    local_state = user_data / "Local State"
    if not local_state.is_file():
        return []
    try:
        active = json.loads(local_state.read_text(encoding="utf-8")).get("profile", {}).get("last_active_profiles") or []
    except (OSError, json.JSONDecodeError):
        return []
    return [name for name in active if isinstance(name, str)]


def _chrome_version() -> str:
    app = Path("/mnt/c/Program Files/Google/Chrome/Application")
    if not app.is_dir():
        return ""
    try:
        names = [path.name for path in app.iterdir() if path.is_dir()]
    except OSError:
        return ""
    return install_version(names)


def _listener_headless(host: str, port: int) -> bool:
    """True when the Chrome listening on this DevTools port was started headless."""
    if port <= 0 or not re.fullmatch(r"[0-9A-Fa-f:.]+", host or ""):
        return False
    script = (
        "$conn = Get-NetTCPConnection -LocalPort "
        + str(port)
        + " -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -eq '"
        + host
        + "' } | Select-Object -First 1; "
        "if (-not $conn) { 'false'; exit }; "
        "$proc = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $conn.OwningProcess); "
        "if ($proc.CommandLine -like '*--headless*') { 'true' } else { 'false' }"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            cwd="/mnt/c/Windows",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "true" in proc.stdout.lower()


def _node_exe() -> str | None:
    windows = Path("/mnt/c/Program Files/nodejs/node.exe")
    if windows.is_file():
        return str(windows)
    return None


class ChromeLink:
    """Poll Chrome and the MCP process, and publish a booth-ready status."""

    def __init__(self, endpoint: str, page_url: str, publish, title: str = "") -> None:
        self._endpoint = endpoint
        self._page_url = page_url
        self._title = title
        self._publish = publish
        self._live_url = ""
        self._live_title = ""
        self._headless: bool | None = None
        self._cdp_blocked = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._sampler: subprocess.Popen | None = None
        self._instance = "Chrome"
        self._profile = "Looking…"
        self._account = ""
        self._bytes_in = 0
        self._bytes_out = 0
        self._clients = 0
        self._identity_ok = False
        self._have_baseline = False
        self._active_until = 0.0
        parsed = parse_browser_endpoint(endpoint)
        self._host = parsed[0] if parsed else ""
        self._port = parsed[1] if parsed else 0

    def start(self) -> None:
        if not self._port:
            self._profile = "Not connected"
            self._publish(self._view())
            return
        identity = threading.Thread(target=self._identity_loop, name="chrome-identity", daemon=True)
        traffic = threading.Thread(target=self._traffic_loop, name="chrome-traffic", daemon=True)
        self._threads = [identity, traffic]
        identity.start()
        traffic.start()

    def stop(self) -> None:
        self._stop.set()
        sampler = self._sampler
        if sampler is not None and sampler.poll() is None:
            sampler.terminate()
        for thread in self._threads:
            thread.join(timeout=2)

    def _view(self) -> dict:
        with self._lock:
            connected = self._identity_ok or self._clients > 0
            view = {
                "connected": connected,
                "active": time.monotonic() < self._active_until,
                "instance": self._instance,
                "profile": self._profile,
                "account": self._account,
                "bytes_in": self._bytes_in,
                "bytes_out": self._bytes_out,
            }
            if self._live_url:
                identity, detail = page_identity(self._live_url, self._live_title or self._title)
                view["page_url"] = self._live_url
                view["page_identity"] = identity
                view["page_detail"] = detail
            return view

    def _identity_loop(self) -> None:
        while not self._stop.is_set():
            self._refresh_identity()
            self._publish(self._view())
            if self._stop.wait(30):
                break

    def _apply_local(self, profiles: list[dict], user_data: Path) -> None:
        """Name the browser and profile without opening a second DevTools socket."""
        version = _chrome_version()
        if self._headless is None:
            self._headless = _listener_headless(self._host, self._port)
        chosen = profile_for_page(user_data, profiles, self._page_url) or preferred_profile(
            profiles, load_last_active(user_data)
        )
        with self._lock:
            if version:
                self._instance = instance_name("Chrome/" + version, "HeadlessChrome" if self._headless else "")
                self._identity_ok = True
            if chosen:
                self._profile = chosen.get("label") or ""
                self._account = chosen.get("email") or ""

    def _refresh_identity(self) -> None:
        appdata = _local_appdata()
        if appdata is None:
            with self._lock:
                self._identity_ok = False
                self._profile = "Not connected"
            return
        user_data = appdata / "Google" / "Chrome" / "User Data"
        profiles = load_chrome_profiles(user_data)
        self._apply_local(profiles, user_data)
        self._publish(self._view())
        if self._cdp_blocked:
            return
        node = _node_exe()
        if node is None:
            return
        emails = [profile["email"] for profile in profiles if profile.get("email")]
        temp = appdata / "Temp"
        temp.mkdir(parents=True, exist_ok=True)
        script = temp / "rhapsode-chrome-probe.mjs"
        script.write_text(_PROBE_JS, encoding="utf-8")
        try:
            proc = subprocess.run(
                [node, _wsl_to_win(script), self._endpoint, self._page_url, json.dumps(emails), self._title],
                capture_output=True,
                text=True,
                cwd="/mnt/c/Windows",
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Chrome answers only one browser socket, and MCP already holds it.
            self._cdp_blocked = True
            return
        payload = _last_json(proc.stdout)
        if not payload or not payload.get("ok"):
            self._cdp_blocked = True
            return
        chosen = match_profile(profiles, payload.get("emails") or [], bool(payload.get("usedDefault")))
        live_url = str(payload.get("pageUrl") or "")
        live_title = str(payload.get("pageTitle") or "")
        product = payload.get("product") or ""
        with self._lock:
            self._identity_ok = True
            if product:
                self._instance = instance_name(product, payload.get("userAgent") or "")
            if live_url:
                self._live_url = live_url
                self._live_title = live_title
            if chosen:
                self._profile = chosen.get("label") or ""
                self._account = chosen.get("email") or ""
            elif not self._profile or self._profile in {"Looking…", "Not connected"}:
                self._profile = "Profile unknown"

    def _traffic_loop(self) -> None:
        appdata = _local_appdata()
        if appdata is None:
            return
        temp = appdata / "Temp"
        temp.mkdir(parents=True, exist_ok=True)
        script = temp / "rhapsode-chrome-link.ps1"
        script.write_text(_SAMPLER_PS1, encoding="utf-8")
        try:
            self._sampler = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    _wsl_to_win(script),
                    "-Port",
                    str(self._port),
                ],
                cwd="/mnt/c/Windows",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError:
            return
        assert self._sampler.stdout is not None
        for line in self._sampler.stdout:
            if self._stop.is_set():
                break
            payload = _last_json(line)
            if not payload:
                continue
            self._apply_traffic(int(payload.get("bytes_in") or 0), int(payload.get("bytes_out") or 0), int(payload.get("clients") or 0))
            self._publish(self._view())

    def _apply_traffic(self, bytes_in: int, bytes_out: int, clients: int) -> None:
        with self._lock:
            self._clients = clients
            if bytes_in == 0 and bytes_out == 0 and (self._bytes_in or self._bytes_out):
                return
            if self._have_baseline and (bytes_in > self._bytes_in or bytes_out > self._bytes_out):
                self._active_until = time.monotonic() + 3
            self._have_baseline = True
            self._bytes_in = bytes_in
            self._bytes_out = bytes_out
            if clients and self._profile == "Looking…":
                self._profile = "Profile unknown"


def _last_json(text: str) -> dict | None:
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None
