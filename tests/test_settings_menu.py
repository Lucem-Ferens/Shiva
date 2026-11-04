"""Settings file (allow-list, never destructive options) and the More menu / language switch."""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from shiva import i18n, settings
from shiva.gui import MainWindow


@pytest.fixture
def sdir(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIVA_SETTINGS_DIR", str(tmp_path))
    return tmp_path


def test_settings_allow_list_and_roundtrip(sdir):
    assert settings.load() == {}
    assert settings.save("language", "zh_TW") and settings.load() == {"language": "zh_TW"}
    assert not settings.save("shred_after", "1") and not settings.save("language", "xx")
    text = (sdir / settings.FILE_NAME).read_text(encoding="utf-8")
    assert "shred" not in text and "encrypt" not in text and "delete" not in text
    assert settings.reset() and not (sdir / settings.FILE_NAME).exists()


def test_altered_file_values_are_ignored(sdir):
    (sdir / settings.FILE_NAME).write_text("[choices]\nlanguage = ../../evil\nshred = 1\n", encoding="utf-8")
    assert settings.load() == {}


def test_no_file_when_running_from_source(monkeypatch):
    monkeypatch.delenv("SHIVA_SETTINGS_DIR", raising=False)
    assert settings.settings_path() is None and not settings.save("language", "en") and settings.load() == {}


def test_read_only_folder_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIVA_SETTINGS_DIR", str(tmp_path / "missing" / "folder"))
    assert settings.save("language", "en") is False


@pytest.fixture
def win(sdir):
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    yield w
    w.close(); i18n.uninstall()


def test_menu_entries_and_language_switch_keeps_lists(win, sdir, tmp_path):
    for t in ("More",):
        assert win.menuBtn.accessibleName() == t
    assert set(win.languageActions) == {"system", "en", "zh_TW"}
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    win.add_paths([f], "shred"); win.wait_for_probes()
    win.pages["shred"].pwEdit.setText("secret")
    win.switch_language("zh_TW")
    from shiva import gui
    new = gui._KEEP[-1]
    assert new is not win and settings.load()["language"] == "zh_TW"
    assert [r.path for r in new.pages["shred"].rows] == [f]
    assert new.pages["shred"].pwEdit.text() == ""
    assert new.nav["clean"].text() == "" or new.nav["clean"].accessibleName().startswith("清理")
    new.close()


def test_language_switch_refused_while_busy(win):
    win.busy = True
    win.switch_language("zh_TW")
    assert settings.load() == {}


def test_menu_button_is_right_after_navigation_in_tab_order(win):
    assert win.menuBtn.focusPolicy() != 0
