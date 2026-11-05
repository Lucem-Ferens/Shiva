"""The cleaning worker sees the file only after Windows has applied its limits; if a limit fails, cleaning stops."""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from shiva import winjob
from shiva.metadata import CleanError, runner

needs_nt = pytest.mark.skipif(os.name != "nt", reason="Windows job objects")


def _jpeg(path: Path) -> None:
    Image.new("RGB", (8, 8), (1, 2, 3)).save(path, "JPEG")


@pytest.fixture
def spawned(monkeypatch):
    """Every worker process started through the runner, so a test can check it was stopped."""
    procs = []
    real = subprocess.Popen

    def spy(*a, **k):
        p = real(*a, **k)
        procs.append(p)
        return p
    monkeypatch.setattr(runner.subprocess, "Popen", spy)
    return procs


@needs_nt
@pytest.mark.parametrize("what", ["limit_none", "adopt_false", "adopt_raises", "limit_raises"])
def test_cleaning_stops_before_reading_the_file_when_a_limit_cannot_be_applied(tmp_path, monkeypatch, spawned, what):
    src = tmp_path / "a.jpg"; _jpeg(src)

    def boom(*a, **k):
        raise OSError("refused")
    if what == "limit_none":
        monkeypatch.setattr(winjob, "adopt_limited", lambda *a, **k: None)
    elif what == "adopt_false":
        monkeypatch.setattr(winjob, "adopt", lambda proc: False)
    elif what == "adopt_raises":
        monkeypatch.setattr(winjob, "adopt", boom)
    else:
        monkeypatch.setattr(winjob, "adopt_limited", boom)
    with pytest.raises(CleanError, match="safety limits"):
        runner.clean_file(src)
    assert [p.name for p in tmp_path.iterdir()] == ["a.jpg"]
    assert len(spawned) == 1 and spawned[0].poll() is not None        # the worker was stopped, not left waiting


@needs_nt
def test_contained_worker_still_cleans(tmp_path):
    src = tmp_path / "a.jpg"; _jpeg(src)
    assert runner.clean_file(src).output.exists()


@needs_nt
def test_a_process_assigned_to_the_kill_on_close_job_is_reported_as_in_a_job():
    p = subprocess.Popen([sys.executable, "-c", "input()"], stdin=subprocess.PIPE)
    try:
        assert winjob.adopt(p) is True and winjob.in_job(p) is True
    finally:
        p.kill(); p.wait()


_SPAWN_AFTER_GO = (
    "import subprocess, sys\n"
    "input()\n"
    "try:\n"
    "    subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
    "    print('spawned')\n"
    "except OSError:\n"
    "    print('blocked')\n"
)


@needs_nt
@pytest.mark.parametrize("max_processes, expected", [(1, "blocked"), (None, "spawned")])
def test_process_limit_stops_the_worker_from_starting_another_process(max_processes, expected):
    p = subprocess.Popen([sys.executable, "-c", _SPAWN_AFTER_GO], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    handle = winjob.adopt_limited(p, 2 * 1024 * 1024 * 1024, max_processes=max_processes)
    try:
        assert handle is not None
        out, _ = p.communicate("go\n", timeout=60)
        assert out.strip() == expected
    finally:
        winjob.close_limited(handle)
        p.kill(); p.wait()
