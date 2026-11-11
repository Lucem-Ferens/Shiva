"""Settings file (allow-list, never destructive options) and the Settings and About pages."""
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


def _settings(win):
    win.nav["settings"].setChecked(True)
    return win.settingsPage


def test_radio_change_alone_does_not_apply(win, sdir):
    from shiva import gui
    n = len(gui._KEEP)
    pg = _settings(win)
    assert pg.selected() == "system" and not pg.applyBtn.isEnabled()
    pg.languageRadios["zh_TW"].setChecked(True)
    assert pg.applyBtn.isEnabled() and settings.load() == {} and len(gui._KEEP) == n
    pg.languageRadios["system"].setChecked(True)
    assert not pg.applyBtn.isEnabled()


def test_apply_persists_rebuilds_and_returns_to_settings(win, sdir, tmp_path):
    from shiva import gui
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    win.add_paths([f], "shred"); win.wait_for_probes()
    win.pages["shred"].pwEdit.setText("secret")
    win.set_tab("shred")
    pg = _settings(win)
    pg.languageRadios["zh_TW"].setChecked(True)
    pg.applyBtn.click()
    new = gui._KEEP[-1]
    assert new is not win and settings.load()["language"] == "zh_TW"
    assert new.view() == "settings" and new.current_tab() == "shred"
    assert new.settingsPage.selected() == "zh_TW" and not new.settingsPage.applyBtn.isEnabled()
    assert [r.path for r in new.pages["shred"].rows] == [f]
    assert new.pages["shred"].pwEdit.text() == ""
    assert new.nav["clean"].accessibleName().startswith("清理")
    new.close()


def test_apply_refused_while_busy(win):
    pg = _settings(win)
    pg.languageRadios["zh_TW"].setChecked(True)
    win.busy = True
    pg.applyBtn.click()
    assert settings.load() == {}


def test_reset_saved_choices_button(win, sdir):
    settings.save("language", "en")
    pg = _settings(win)
    pg.resetBtn.click()
    assert settings.load() == {} and "reset" in win.statusLine.text()


def test_sidebar_pages_and_nav_lock(win):
    assert [win.nav[k].accessibleName().split(",")[0] for k in ("settings", "about")] == ["Settings", "About"]
    win.nav["about"].setChecked(True)
    assert win.view() == "about" and win.current_tab() == "clean"
    assert win.aboutPage.versionLabel.text().startswith("Version ")
    from PySide6.QtWidgets import QLabel
    assert any("GPL-3.0-or-later" in c.text() for c in win.aboutPage.findChildren(QLabel))
    win.busy = True; win._set_busy(True)
    assert not win.nav["settings"].isEnabled() and not win.nav["about"].isEnabled()
    win._set_busy(False)


def test_files_are_not_added_from_settings_or_about(win, tmp_path):
    f = tmp_path / "a.txt"; f.write_bytes(b"x")
    _settings(win)
    assert win.add_paths([f]) == 0 and not win.pages["clean"].rows
    assert "Files can't be added here" in win.statusLine.text()
    assert win.add_paths([f], "shred") == 1      # an explicit function page still works


def test_sidebar_is_one_tab_stop_group(win):
    assert all(win.nav[k].autoExclusive() for k in ("clean", "encrypt", "shred", "settings", "about"))
    assert not hasattr(win, "menuBtn")
