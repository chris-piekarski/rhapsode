"""Coverage for agent.Booth class and additional agent_view edges."""
from __future__ import annotations

import pytest

from rhapsode.agent import Booth, agent_view, interpret_command


def test_booth_init():
    booth = Booth("https://example.com/page")
    assert booth.base == "https://example.com/page"


def test_booth_page_url():
    booth = Booth("https://learning.oreilly.com/ch02")
    assert booth.base == "https://learning.oreilly.com/ch02"


def test_agent_view_no_lines():
    session = {"title": "Empty", "lines": []}
    view = agent_view(session)
    assert view["progress"] == {}
    assert view["line"] is None


def test_agent_view_single_done():
    session = {
        "title": "Done",
        "lines": [{"text": "Hello.", "kind": "p", "status": "done", "start": 0.0, "end": 5.0}],
    }
    view = agent_view(session)
    assert view["progress"].get("done") == 1
    assert view["line"] is None


def test_interpret_command_bad_transport():
    with pytest.raises(ValueError, match="transport"):
        interpret_command({"type": "transport", "action": "explode"}, 1.0)


def test_interpret_command_unknown_type():
    with pytest.raises(ValueError, match="unknown command"):
        interpret_command({"type": "unknown"}, 1.0)


def test_interpret_command_play():
    assert interpret_command({"type": "transport", "action": "play"}, 0.8) == {"transport": "playing"}


def test_interpret_command_speed():
    assert interpret_command({"type": "speed", "value": 1.5}, 0.8) == {"speed": 1.5}
