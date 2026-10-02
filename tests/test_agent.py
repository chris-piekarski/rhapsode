"""Agent view of the booth and the commands it accepts."""

import pytest

from rhapsode.agent import agent_view, interpret_command


def test_agent_view_names_the_line_being_read():
    session = {
        "title": "Chapter 2. Designing Agent Systems",
        "transport": "playing",
        "speed": 0.8,
        "voice": "af_heart",
        "place": {"playhead": 12.5},
        "source": {
            "label": "https://learning.oreilly.com/library/view/book/ch02.html",
            "detail": "Chapter 2. Designing Agent Systems",
        },
        "mcp": {"profile": "example.com", "account": "ada@example.com", "connected": True, "active": False},
        "lines": [
            {"text": "Earlier.", "kind": "p", "status": "done", "start": 0.0, "end": 10.0},
            {"text": "Most practitioners begin here.", "kind": "p", "status": "done", "start": 10.0, "end": 20.0},
            {"text": "Next.", "kind": "p", "status": "pending", "start": 20.0, "end": 30.0},
        ],
    }
    view = agent_view(session)
    assert view["transport"] == "playing"
    assert view["page_url"].endswith("/ch02.html")
    assert view["profile"] == "example.com"
    assert view["line"]["index"] == 1
    assert "Most practitioners" in view["line"]["text"]
    assert view["nearby"][1]["current"] is True
    assert view["progress"] == {"done": 2, "pending": 1}


def test_interpret_command_accepts_booth_controls():
    assert interpret_command({"type": "transport", "action": "play"}, 0.8) == {"transport": "playing"}
    assert interpret_command({"type": "speed", "value": 1.2}, 0.8) == {"speed": 1.2}
    assert interpret_command({"type": "seek", "seconds": 42}, 0.8) == {"seek": 42.0}
    assert interpret_command({"type": "voice", "value": "af_heart", "line": 3}, 0.8) == {"voice": "af_heart", "line": 3}
    with pytest.raises(ValueError):
        interpret_command({"type": "transport", "action": "rewind"}, 0.8)
    with pytest.raises(ValueError):
        interpret_command({"type": "voice", "value": "not-a-voice"}, 0.8)
