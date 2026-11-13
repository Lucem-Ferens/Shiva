"""Command-line words must not reach Qt: Qt would apply options such as -reverse or -platformpluginpath."""
import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")

PROBE = (
    "import sys\n"
    "sys.argv = ['shiva', '-reverse', '-stylesheet', 'missing.qss', '--smoke']\n"
    "from shiva.gui import window\n"
    "rc = window.main()\n"
    "from PySide6.QtWidgets import QApplication\n"
    "from PySide6.QtCore import Qt\n"
    "app = QApplication.instance()\n"
    "print('DIR', 'rtl' if app.layoutDirection() == Qt.RightToLeft else 'ltr')\n"
    "print('ARGS', app.arguments()[1:])\n"
    "sys.exit(rc)\n"
)


def test_qt_options_on_the_command_line_are_ignored(tmp_path):
    env = dict(os.environ, PYTHONPATH=SRC, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1", SHIVA_LANG="en")
    r = subprocess.run([sys.executable, "-c", PROBE], cwd=tmp_path, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    assert "SMOKE_OK" in r.stdout
    assert "DIR ltr" in r.stdout
    assert "ARGS []" in r.stdout
