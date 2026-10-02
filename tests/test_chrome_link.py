"""Targeted coverage for chrome_link and agent modules."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


from rhapsode.chrome_link import (  # noqa: F401
    ChromeLink,
    blank_mcp,
    install_version,
    instance_name,
    load_chrome_profiles,
    load_last_active,
    match_profile,
    page_identity,
    preferred_profile,
)


# --- load_chrome_profiles ---

def _fake_local_state(user_data: Path, info_cache: dict[str, Any]) -> None:
    ls = user_data / "Local State"
    ls.parent.mkdir(parents=True, exist_ok=True)
    ls.write_text(json.dumps({"profile": {"info_cache": info_cache}}), encoding="utf-8")


def _fake_preferences(user_data: Path, directory: str, email: str | None = None) -> None:
    p = user_data / directory / "Preferences"
    p.parent.mkdir(parents=True, exist_ok=True)
    accounts = [{"email": email}] if email else []
    p.write_text(json.dumps({"account_info": accounts}), encoding="utf-8")


def test_load_chrome_profiles_empty(tmp_path: Path) -> None:
    _fake_local_state(tmp_path, {})
    assert load_chrome_profiles(tmp_path) == []


def test_load_chrome_profiles_single(tmp_path: Path) -> None:
    _fake_local_state(tmp_path, {"Default": {"name": "Fred"}})
    _fake_preferences(tmp_path, "Default", "fred@example.com")
    profiles = load_chrome_profiles(tmp_path)
    assert len(profiles) == 1
    assert profiles[0]["directory"] == "Default"
    assert profiles[0]["label"] == "Fred"
    assert profiles[0]["email"] == "fred@example.com"


def test_load_chrome_profiles_missing_prefs(tmp_path: Path) -> None:
    _fake_local_state(tmp_path, {"Default": {"name": "Ginny"}})
    profiles = load_chrome_profiles(tmp_path)
    assert profiles[0]["email"] == ""


def test_load_chrome_profiles_no_local_state(tmp_path: Path) -> None:
    assert load_chrome_profiles(tmp_path) == []


def test_load_chrome_profiles_bad_json(tmp_path: Path) -> None:
    (tmp_path / "Local State").write_text("not-json", encoding="utf-8")
    assert load_chrome_profiles(tmp_path) == []


# --- load_last_active ---

def test_load_last_active_empty(tmp_path: Path) -> None:
    _fake_local_state(tmp_path, {})
    assert load_last_active(tmp_path) == []


def test_load_last_active_with_profiles(tmp_path: Path) -> None:
    _fake_local_state(tmp_path, {
        "Default": {"name": "A"},
        "Profile 1": {"name": "B"},
    })
    # lastActive key
    ls = tmp_path / "Local State"
    cached = json.loads(ls.read_text())
    cached["profile"]["last_active_profiles"] = ["Profile 1"]
    ls.write_text(json.dumps(cached))
    assert load_last_active(tmp_path) == ["Profile 1"]


# --- helpers ---


def test_install_version_empty() -> None:
    assert install_version([]) == ""


def test_install_version_single() -> None:
    assert install_version(["1.0.0"]) == "1.0.0"


def test_install_version_picks_newest() -> None:
    assert install_version(["1.0", "2.0.3"]) == "2.0.3"


def test_instance_name_headless() -> None:
    assert instance_name("Chrome", "HeadlessChrome/1.0") == "Headless Chrome"


# --- page_identity ---


def test_page_identity_strips_scheme() -> None:
    addr, title = page_identity("https://learning.oreilly.com/ch01", "Chapter 1")
    assert "learning.oreilly.com" in addr
    assert title == "Chapter 1"


def test_page_identity_no_title() -> None:
    addr, title = page_identity("https://example.com/")
    assert title == ""


# --- blank_mcp ---


def test_blank_mcp_with_endpoint() -> None:
    mcp = blank_mcp(True)
    assert mcp["instance"] == "Chrome"


def test_blank_mcp_without_endpoint() -> None:
    mcp = blank_mcp(False)
    assert not mcp.get("connected")


# --- preferred_profile ---


def test_preferred_profile_none() -> None:
    assert preferred_profile([], []) is None


def test_preferred_profile_match() -> None:
    profiles = [{"directory": "Default", "label": "A"}]
    result = preferred_profile(profiles, ["Default"])
    assert result is not None and result["directory"] == "Default"


# --- match_profile ---


def test_match_profile_emails() -> None:
    profiles = [{"directory": "D", "email": "a@b.com"}]
    assert match_profile(profiles, ["a@b.com"], False) is not None


def test_match_profile_fallback_first() -> None:
    profiles = [{"directory": "Default", "label": "X"}, {"directory": "Profile 1"}]
    result = match_profile(profiles, [], True)
    assert result is not None and result["directory"] == "Default"


# --- ChromeLink ---



def test_chromelink_init():
    views = []
    link = ChromeLink(
        endpoint="http://127.0.0.1:9222",
        page_url="https://example.com/p",
        publish=views.append,
        title="Test Page",
    )
    assert link._endpoint
    views.clear()


def test_chromelink_view_defaults():
    link = ChromeLink(
        endpoint="http://127.0.0.1:9222",
        page_url="https://example.com/p",
        publish=lambda v: None,
    )
    link._identity_ok = True
    view = link._view()
    assert view["connected"] is True
    assert view["instance"] == "Chrome"


def test_chromelink_stop_no_threads():
    link = ChromeLink("", "", lambda v: None)
    # start with empty port -> sets profile to "Not connected"
    link.start()
    link.stop()


def test_chromelink_profile_for_page_none():
    from rhapsode.chrome_link import profile_for_page
    result = profile_for_page(Path("/tmp"), [], "")
    assert result is None


# --- _chrome_version ---

def test_chrome_version_no_dir(monkeypatch, tmp_path):
    from rhapsode.chrome_link import _chrome_version

    monkeypatch.setattr("rhapsode.chrome_link._CHROME_APP", tmp_path / "missing")
    assert _chrome_version() == ""

    app = tmp_path / "Application"
    (app / "154.0.8037.58").mkdir(parents=True)
    (app / "154.0.8037.93").mkdir()
    (app / "PlatformExperienceHelper").mkdir()
    monkeypatch.setattr("rhapsode.chrome_link._CHROME_APP", app)
    assert _chrome_version() == "154.0.8037.93"


# --- _node_exe ---

def test_node_exe_no_install(monkeypatch, tmp_path):
    from rhapsode.chrome_link import _node_exe

    missing = tmp_path / "node.exe"
    monkeypatch.setattr("rhapsode.chrome_link._NODE_WINDOWS", missing)
    assert _node_exe() is None
    missing.write_bytes(b"")
    assert _node_exe() == str(missing)
