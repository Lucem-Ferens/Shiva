"""The window treats the cleaning helper's answer as untrusted: bounded size, checked header, only the fields it uses."""
import json
import struct
import subprocess
import sys

import pytest

from shiva.metadata import CleanError, runner, worker


def frame(header, payload=b""):
    h = json.dumps(header).encode()
    return struct.pack(">I", len(h)) + h + payload


def test_parse_frame_rejects_malformed_frames():
    for bad in (b"", b"abc", struct.pack(">I", 10) + b"{}",                                    # short, truncated
                struct.pack(">I", worker.MAX_HEADER + 1) + b"x" * 8,                           # absurd length
                frame([1, 2]), struct.pack(">I", 4) + b"null"):                               # not an object
        with pytest.raises(ValueError):
            worker.parse_frame(bad)
    assert worker.parse_frame(frame({"ok": True}, b"data")) == ({"ok": True}, b"data")


def test_sanitize_keeps_only_expected_fields_and_types():
    huge = "x" * 100000
    h = worker.sanitize_response({
        "ok": "yes", "kind": "weird", "error": huge, "code": huge, "removed": ["a", 7, None, {"b": 1}] + ["z"] * 1000,
        "kept": "not a list", "warnings": [huge], "extra": "dropped", "categories": [
            {"key": "k", "label": huge, "detail": "d", "content_changing": True, "n": 3, "nested": {"a": 1}, "lst": [1]}, "no", 5]})
    assert h["ok"] is False and h["kind"] is None and "extra" not in h
    assert len(h["error"]) == worker.MAX_TEXT and len(h["code"]) == 64
    assert h["removed"][:2] == ["a", "z"] or h["removed"][0] == "a"
    assert len(h["removed"]) <= worker.MAX_ITEMS and h["kept"] == []
    assert len(h["warnings"][0]) == worker.MAX_TEXT
    cat = h["categories"][0]
    assert cat["key"] == "k" and len(cat["label"]) == worker.MAX_TEXT and cat["content_changing"] is True and cat["n"] == 3
    assert "nested" not in cat and "lst" not in cat and len(h["categories"]) == 1
    assert worker.sanitize_response({"ok": True})["ok"] is True


def test_sanitize_omits_an_empty_message_so_callers_keep_their_default():
    h = worker.sanitize_response({"ok": False, "kind": "error", "error": "", "code": None})
    assert "error" not in h and "code" not in h
    assert h.get("error", "cleaning failed") == "cleaning failed"


def test_sanitize_drops_categories_the_window_could_not_show():
    good = {"key": "k", "label": "L", "detail": "d", "content_changing": False, "selectable": True}
    h = worker.sanitize_response({"ok": True, "categories": [good, {"key": "k"}, {"label": "x", "detail": "d", "key": 1,
                                                                         "content_changing": False}, good | {"content_changing": "yes"}]})
    assert h["categories"] == [good]


def _proc(code):
    return subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE)


def test_talk_stops_a_helper_that_answers_with_too_much(monkeypatch):
    monkeypatch.setattr(runner, "MIN_RESPONSE_CAP", 1024 * 1024)
    proc = _proc("import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x' * 50000000)")
    with pytest.raises(CleanError, match="far more data"):
        runner._talk(proc, {"op": "scan", "format": "png"}, b"", None, 60)
    assert proc.poll() is not None


def test_talk_rejects_an_unreadable_answer():
    proc = _proc("import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'not a frame at all')")
    with pytest.raises(CleanError, match="could not be read"):
        runner._talk(proc, {"op": "scan", "format": "png"}, b"", None, 60)


def test_talk_returns_a_sanitized_answer():
    code = ("import sys, json, struct; sys.stdin.buffer.read(); h = json.dumps({'ok': True, 'removed': ['a'], 'evil': 1}).encode(); "
            "sys.stdout.buffer.write(struct.pack('>I', len(h)) + h + b'DATA')")
    hdr, payload = runner._talk(_proc(code), {"op": "clean", "format": "png"}, b"in", None, 60)
    assert hdr["ok"] is True and hdr["removed"] == ["a"] and "evil" not in hdr and payload == b"DATA"
