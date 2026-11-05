"""The dependency lock: every package pinned with hashes, and the build refuses a differing environment."""
from __future__ import annotations

import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_build():
    spec = importlib.util.spec_from_file_location("shiva_build_tool", ROOT / "tools" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = _load_build()
DEPS = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_every_pin_in_the_lock_carries_hashes():
    text = (ROOT / "requirements" / "lock.txt").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9][A-Za-z0-9._-]*==)", text.strip())
    assert len(blocks) >= 10
    for block in blocks:
        assert "--hash=sha256:" in block, block.splitlines()[0]


def test_direct_requirements_are_pinned_in_the_lock_with_the_same_version():
    lock = build.locked_versions()
    wanted = re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", (ROOT / "requirements" / "lock.in").read_text(encoding="utf-8"), re.M)
    assert wanted
    for name, version in wanted:
        assert lock[_norm(name)] == version


def test_runtime_dependencies_and_what_they_pull_in_are_all_locked():
    lock = build.locked_versions()
    for name in build.dependency_versions(DEPS):
        assert _norm(name) in lock, name


def test_build_stops_when_an_installed_version_differs_from_the_lock(monkeypatch, capsys):
    real = build.md.version
    monkeypatch.setattr(build.md, "version", lambda n: "0.0.0" if _norm(n) == "pillow" else real(n))
    with pytest.raises(SystemExit):
        build.check_environment(DEPS)
    assert "pillow" in capsys.readouterr().err


def test_build_stops_when_a_locked_package_is_not_installed(monkeypatch, capsys):
    real = build.md.version

    def fake(n):
        if _norm(n) == "pikepdf":
            raise build.md.PackageNotFoundError(n)
        return real(n)

    monkeypatch.setattr(build.md, "version", fake)
    with pytest.raises(SystemExit):
        build.check_environment(DEPS)
    assert "pikepdf" in capsys.readouterr().err
