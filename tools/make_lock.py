"""Regenerate requirements/lock.txt (every package with SHA-256 hashes) from requirements/lock.in.

Usage:  python -m pip install pip-tools   (in a throw-away environment)
        python tools/make_lock.py

The lock lists the files of each pinned version for every platform PyPI offers; installing with
`pip install --require-hashes -r requirements/lock.txt` accepts only those files. tools/build.py refuses
to build when the running environment differs from the lock.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    cmd = [sys.executable, "-m", "piptools", "compile", "--generate-hashes", "--allow-unsafe", "--no-header",
           "--strip-extras", "--quiet", "--output-file", "requirements/lock.txt",
           "requirements/lock.in"]
    return subprocess.run(cmd, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
