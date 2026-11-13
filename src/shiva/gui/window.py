"""Main window: pages, navigation, runs.
"""
from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import output, settings, shred, steps, storage, tokens
from ..metadata import CleanError, runner
from ..clean_pipeline import JPEG_CHANGES
from .. import ui_model as m
from ..i18n import dyn, tr
from .dialogs import AboutShreddingDialog, ConfirmDialog, PdfDialog
from .info_pages import AboutPage, SettingsPage
from .page import _Row, TabPage
from .widgets import DimLayer, NavButton, _announce, _urls, apply_theme

_OFFICE_FMTS = ("docx", "xlsx", "pptx", "odt", "ods", "odp")   # formats with an Office category dialog


class _Worker(QObject):
    line = Signal(str)
    done = Signal(list)
    prog = Signal(int, int)

    def __init__(self, paths, passphrase, opts):
        super().__init__()
        self._a = (paths, passphrase, opts)
        self.cancel = threading.Event()

    def run(self):
        paths, pw, opts = self._a
        self._a = None  # drop our reference to the passphrase
        results = []
        try:
            results = steps.run_steps(paths, pw, opts, self.line.emit, self.cancel, self.prog.emit)
        except Exception as e:  # never leave the window stuck in "running"
            self.line.emit(f"Unexpected error: {type(e).__name__}: {e}")
        finally:
            self.done.emit(results)


class _ProbeBridge(QObject):
    done = Signal(object, str, object)   # page, row key, Facts


# ------------------------------------------------------------------ window
class MainWindow(QMainWindow):
    pageStateChanged = Signal()
    runStarted = Signal(object, list)
    runFinished = Signal(list)

    def __init__(self):
        super().__init__()
        self.clock = time.monotonic   # injectable in tests (elapsed seconds while running)
        self.setWindowTitle("Shiva")
        self.resize(960, 640); self.setMinimumSize(720, 440)
        self.setAcceptDrops(True)
        self._thread: QThread | None = None
        self._worker = None
        self._closing = False
        self._scan_cancel = threading.Event()      # set when the window is closed during a category scan
        self.busy = False
        self._log: list[str] = []
        self._seen_changes: dict[str, list[str]] = {}
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._assess_cache: dict = {}
        self._bridge = _ProbeBridge()
        self._bridge.done.connect(self._probe_done)

        # --- top navigation: the three functions
        self.tabBar = QWidget(); self.tabBar.setObjectName("side"); self.tabBar.setFixedWidth(tokens.SIDEBAR_W)
        nl = QVBoxLayout(self.tabBar); nl.setContentsMargins(tokens.SPACE[2], tokens.SPACE[4], tokens.SPACE[2], tokens.SPACE[2])
        nl.setSpacing(tokens.SPACE[1])
        brand = QLabel("Shiva"); brand.setObjectName("brand"); nl.addWidget(brand)
        self.nav: dict[str, NavButton] = {}
        for t in m.TABS:
            b = NavButton(t); self.nav[t] = b
            nl.addWidget(b)
            b.toggled.connect(lambda on, tab=t: on and self._select(tab))
            b.dropped.connect(lambda paths, tab=t: self.nav_drop(tab, paths))
        nl.addStretch(1)
        for t in m.INFO_KEYS:       # Settings and About: same exclusive group, so the sidebar stays one tab stop
            b = NavButton(t); self.nav[t] = b
            nl.addWidget(b)
            b.toggled.connect(lambda on, tab=t: on and self._select(tab))
            b.dropped.connect(lambda _paths: self._on_info_page())
        self._last_tab = "clean"     # the function page that file actions refer to
        self.stack = QStackedWidget()
        self.pages: dict[str, TabPage] = {}
        for t in m.TABS:
            self.pages[t] = TabPage(t, self); self.stack.addWidget(self.pages[t])
        self.settingsPage = SettingsPage(self); self.stack.addWidget(self.settingsPage)
        self.aboutPage = AboutPage(self); self.stack.addWidget(self.aboutPage)
        root = QWidget(); rl = QHBoxLayout(root); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0)
        rl.addWidget(self.tabBar); rl.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        # one status line for the window; it moves into the page that is showing, next to that page's action button
        self.statusLine = QLabel(tr("Ready")); self.statusLine.setAccessibleName(tr("Status")); self.statusLine.setWordWrap(True)
        self._status_slot = None
        self._move_status(m.TABS[0])
        QWidget.setTabOrder(self.nav["clean"], self.nav["encrypt"])
        QWidget.setTabOrder(self.nav["encrypt"], self.nav["shred"])
        QWidget.setTabOrder(self.nav["shred"], self.nav["settings"])
        QWidget.setTabOrder(self.nav["settings"], self.nav["about"])
        QWidget.setTabOrder(self.nav["about"], self.pages["clean"].addFilesBtn)

        for i, t in enumerate(m.TABS, 1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda tab=t: self._ctrl_tab(tab))
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.pick_files)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, activated=self.pick_folder)
        for k in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(k), self, activated=self._press_primary)
        self.nav["clean"].setChecked(True)

    # ---- language, saved choices
    def switch_language(self, code: str) -> None:
        """Rebuild the window in the chosen language: file lists are kept, options and passphrases are reset."""
        if self.busy:
            self.set_status(tr("Wait for the current run to finish."))
            return
        if code not in settings.ALLOWED["language"]:
            return
        saved = settings.save("language", code)
        from .. import i18n
        i18n.install(None if code == "system" else code)
        new = MainWindow()
        new.setGeometry(self.geometry())
        for tab, page in self.pages.items():
            page.take_passphrase()
            page.forget_keys()
            if page.rows:
                new.pages[tab].add_paths([r.path for r in page.rows])
        new.nav[self._last_tab].setChecked(True)
        view = self.view()
        new.nav[view].setChecked(True)
        if view == "settings":
            new.settingsPage.focus_language()
        _KEEP.append(new)
        new.show()
        if not saved and settings.settings_path() is not None:
            new.set_status(tr("The language is used for this run only.") + " "
                           + tr("The settings file can't be written."))
        self._closing = False
        self.close()

    def reset_saved_choices(self) -> None:
        ok = settings.reset()
        if ok:
            self.settingsPage.saved_reset()
            self.set_status(tr("Saved choices were reset.") + " " + tr("This window keeps its language until you restart."))
        else:
            self.set_status(tr("Saved choices could not be reset.") + " " + tr("The settings file can't be deleted."))

    # ---- navigation and status
    def view(self) -> str:
        """The page shown: a function tab, "settings" or "about"."""
        return (m.TABS + m.INFO_KEYS)[self.stack.currentIndex()]

    def current_tab(self) -> str:
        """The function page that file actions refer to (the last one shown while Settings or About is open)."""
        return self._last_tab

    def current_page(self) -> TabPage:
        return self.pages[self.current_tab()]

    def _move_status(self, tab: str) -> None:
        page = self.pages.get(tab) or (self.settingsPage if tab == "settings" else self.aboutPage)
        if self._status_slot is not None:
            self._status_slot.removeWidget(self.statusLine)
        page.statusSlot.addWidget(self.statusLine)
        self._status_slot = page.statusSlot
        self.statusLine.setVisible(self.statusLine.text() not in ("", tr("Ready")))      # "Ready" says nothing: no line

    def _select(self, tab: str) -> None:
        self.stack.setCurrentIndex((m.TABS + m.INFO_KEYS).index(tab))
        self._move_status(tab)
        if tab in self.pages:
            self._last_tab = tab
            self.pages[tab].update_controls()

    def _on_info_page(self) -> bool:
        if self.view() in m.INFO_KEYS:
            self.set_status(tr("Files can't be added here.") + " " + tr("Choose Clean metadata, Encrypt or Shred."))
            return True
        return False

    def _ctrl_tab(self, tab: str) -> None:
        if self.busy:
            self._say_busy()
            return
        self.nav[tab].setChecked(True)

    def set_tab(self, tab: str) -> None:
        self.nav[tab].setChecked(True)

    def set_status(self, text: str) -> None:
        text = dyn(text)
        self.statusLine.setText(text)
        self.statusLine.setVisible(text not in ("", tr("Ready")))
        _announce(self.statusLine, text)

    def _say_busy(self) -> None:
        """A shortcut or button that does nothing during a run says why."""
        self.set_status(tr("Wait for the current run to finish."))

    def start(self) -> None:
        """Press the primary button of the current page (does nothing, with the reason in the status line, if disabled)."""
        if self._on_info_page():
            return
        self.current_page().start()

    def _press_primary(self) -> None:
        if self.busy:
            self._say_busy()
            return
        page = self.current_page()
        b = page.primaryBtn
        if b.isEnabled() and b.isVisible():
            b.click()
        elif b.isVisible():
            page.start()      # disabled: puts the reason in the status line

    def _set_busy(self, on: bool) -> None:
        self.busy = on
        for b in self.nav.values():
            b.setEnabled(not on)
        self.current_page().body.setEnabled(not on)
        self.current_page().update_controls()

    # ---- adding files (one entry point for drop, buttons and Ctrl+O)
    def add_paths(self, paths: list[Path], tab: str | None = None, via_nav: bool = False) -> int:
        if self.busy:
            self.set_status(tr("Wait for the current run to finish."))
            return 0
        if tab is None and self._on_info_page():
            return 0
        tab = tab or self.current_tab()
        had_results = self.pages[tab].mode == "results"
        added, dups = self.pages[tab].add_paths([Path(p) for p in paths])
        parts = []
        if added and had_results:
            parts.append("Previous results cleared")     # adding to a finished run starts a new list
        if added:
            parts.append(f"{added} added to {m.TITLES[tab][0]}" if via_nav else f"{m.unit(added, 'file')} added"
                         if tab == "clean" else f"{m.unit(added, 'item')} added")
        if dups:
            parts.append(f"{dups} already in the list")
        if parts:
            self.set_status(". ".join(parts))
        return added

    def nav_drop(self, tab: str, paths: list[Path]) -> None:
        if self.busy:
            self.set_status(tr("Wait for the current run to finish."))
            return
        self.set_tab(tab)
        self.add_paths(paths, tab, via_nav=True)

    def pick_files(self) -> None:
        if self.busy:
            self._say_busy()
            return
        if self._on_info_page():
            return
        files, _ = QFileDialog.getOpenFileNames(self, tr("Add files"))
        if files:
            self.add_paths([Path(f) for f in files])

    def pick_folder(self) -> None:
        if self.busy:
            self._say_busy()
            return
        if self._on_info_page():
            return
        if self.current_tab() == "clean":
            self.set_status(tr("Folders can't be cleaned yet."))
            return
        d = QFileDialog.getExistingDirectory(self, tr("Add folder"))
        if d:
            self.add_paths([Path(d)])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            if self.view() in self.pages:
                self.pages[self.current_tab()].set_drop_active(True, refused=self.busy)
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dragLeaveEvent(self, e):
        for p in self.pages.values():
            p.set_drop_active(False)

    def dropEvent(self, e):
        for p in self.pages.values():
            p.set_drop_active(False)
        self.add_paths(_urls(e.mimeData()))
        e.acceptProposedAction()

    # ---- background probes
    def submit_probe(self, page: TabPage, row: _Row, nc: bool, ns: bool, ncount: bool) -> None:
        base, key = row.facts, row.key

        def job():
            try:
                res = m.probe(base, nc, ns, ncount, self._assess_cache)
            except Exception:
                res = replace(base, probed=True, classified=True, storage_done=True, counted=True,
                              class_error="Error")
            try:
                self._bridge.done.emit(page, key, res)
            except RuntimeError:
                pass
        try:
            self._pool.submit(job)
        except RuntimeError:
            row.busy = False

    def _probe_done(self, page, key: str, facts) -> None:
        page.apply_probe(key, facts)

    def wait_for_probes(self, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while any(p.pending() for p in self.pages.values()):
            if time.monotonic() > end:
                return False
            QApplication.processEvents(); time.sleep(0.005)
        QApplication.processEvents()
        return True

    # ---- dialog hooks (tests replace these)
    def choose_pdf_categories(self, name: str, cats: list[dict], remaining: int):
        """Returns (chosen keys, use for other PDFs) or None when cancelled."""
        dlg = PdfDialog(self, name, cats, remaining)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.chosen(), dlg.useOthersCheck.isChecked()

    def choose_office_categories(self, name: str, cats: list[dict], remaining: int):
        """Same as choose_pdf_categories for Office/ODF documents."""
        dlg = PdfDialog(self, name, cats, remaining, kind="office")
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.chosen(), dlg.useOthersCheck.isChecked()

    def show_about_shredding(self) -> None:
        AboutShreddingDialog(self).exec()

    def confirm_shred(self, items: list[str], notes: list[str], changes: dict[str, list[str]], opts,
                      item_tips: list[str] | None = None) -> bool:
        """items: one line per item; a line starting with a tab is a per-item Warning under the item above it.
        notes: extra lines under the list. The one-line 'what helps' is added only if some item has a Warning."""
        n = sum(1 for i in items if not i.startswith("\t"))
        risky = any(i.startswith("\t") for i in items)
        title, body, do = m.shred_dialog_text(opts.clean, opts.encrypt, n, opts.unknown_members == "omit")
        notes = list(notes) + m.confirm_notes(opts.clean, opts.unknown_members == "omit")
        notes += [f"{Path(p).name}: will also remove {', '.join(cs)} (changes content)." for p, cs in changes.items()]
        dlg = ConfirmDialog(self, title, body, items, notes, do, trailer=m.TRAILER if risky else "",
                            learn_link=m.WHY_LINK, item_tips=item_tips)
        dlg.learnRequested.connect(self.show_about_shredding)
        layer = DimLayer(self)        # the page behind the question recedes; removed again whatever happens
        try:
            return dlg.exec() == QDialog.Accepted
        finally:
            layer.remove()

    # ---- run preparation
    def _scan_responsively(self, p: Path) -> list[dict]:
        """Scan in a thread and keep the window alive meanwhile (a hostile PDF can take up to the worker timeout)."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(runner.scan_categories, p, self._scan_cancel)
            self.set_status(f"Scanning {p.name} ...")
            while not fut.done():
                QApplication.processEvents()
                time.sleep(0.02)
            return fut.result()

    def _choose_pdfs(self, run_rows: list[_Row], opts) -> bool:
        """One dialog per PDF and per Office/ODF document. 'Use for the other PDFs/documents' is applied only to
        files of the same kind whose found categories were all shown in that dialog; any other file gets its own
        dialog. False = the user cancelled the whole run."""
        self._seen_changes = {}
        for kind, fmts in (("pdf", ("pdf",)), ("office", _OFFICE_FMTS)):
            if not self._choose_kind(run_rows, opts, fmts,
                                     self.choose_pdf_categories if kind == "pdf" else self.choose_office_categories):
                return False
        return True

    def _choose_kind(self, run_rows: list[_Row], opts, fmts, choose) -> bool:
        files = [r for r in run_rows if r.facts.fmt_status == "supported" and r.facts.fmt in fmts]
        shown: set[str] = set(); picked: set[str] = set(); reuse = False
        for i, r in enumerate(files):
            p = r.path
            try:
                cats = self._scan_responsively(p)
            except (CleanError, OSError) as e:
                if self._closing:       # the window was closed during the scan: the whole run is cancelled
                    return False
                self.set_status(f"{p.name}: cannot be scanned ({e}); it will be reported as failed.")
                continue
            if self._closing:
                return False
            if not any(c.get("selectable", True) for c in cats):
                opts.pdf_categories[str(p)] = []
                continue
            keys = {c["key"] for c in cats}
            if reuse and keys <= shown:
                chosen = [c["key"] for c in cats if c["key"] in picked]
            else:
                res = choose(p.name, cats, len(files) - i - 1)
                if res is None:
                    return False
                chosen, others = res
                if others:
                    reuse, shown, picked = True, keys, set(chosen)
            opts.pdf_categories[str(p)] = chosen
            seen = [c["label"] for c in cats if c["key"] in chosen and c["content_changing"]]
            if seen:
                self._seen_changes[str(p)] = seen
        return True

    def _risk_lines(self, row: _Row) -> list[str]:
        """Per-item Warning sentences (drive type, cloud folder). Assessed now if the background probe has not run."""
        f = row.facts
        special = f.special if f.probed else shred.special_kind_of(row.path)
        if not f.storage_done:
            try:
                info = storage.assess(row.path)
            except Exception:
                info = storage.StorageInfo(row.path)
            return m.risk_lines(info.drive_kind, info.cloud_provider, special, f.is_dir)
        return m.risk_lines(f.drive_kind, f.cloud_provider, special, f.is_dir)

    def begin_run(self, page: TabPage, opts, run_rows: list[_Row]) -> None:
        if self.busy or not run_rows:
            return
        self._set_busy(True)
        started = False
        try:
            if opts.clean and not self._choose_pdfs(run_rows, opts):
                return self.set_status("Cancelled. Nothing was changed.")
            out_notes: list[str] = []
            if opts.out_dir is not None:
                try:
                    output.check_out_dir(opts.out_dir)
                    info = storage.assess(opts.out_dir)
                except (output.OutputFolderError, OSError) as e:
                    return self.set_status(f"{e} Nothing was changed.")
                except Exception:
                    info = storage.StorageInfo(opts.out_dir)
                decrypting = opts.decrypt_age and any(r.path.suffix.lower() == ".age" for r in run_rows)
                plaintext = decrypting or (opts.clean and not opts.encrypt)
                out_notes = m.out_dir_notes(info, plaintext)
            unclean = [r for r in run_rows if r.info.can_unclean]
            if opts.shred:
                shred_rows = [r for r in run_rows if not r.info.can_unclean]
                items, tips = m.confirm_item_lines([
                    (m.display_path(r.path) + (f" ({r.facts.count} items)" if r.facts.is_dir and r.facts.count else ""),
                     str(r.path), self._risk_lines(r)) for r in shred_rows])
                changes = dict(self._seen_changes) if opts.clean else {}
                notes = list(out_notes)
                holders = [r for r in shred_rows if opts.clean and opts.jpeg_mode == "reencode"
                           and r.facts.fmt_status == "supported" and r.facts.fmt in m.JPEG_HOLDERS]
                if holders:
                    notes.append(m.NOTE_JPEG)       # one line names the loss, so the changes need no per-file line
                if unclean:
                    notes.append("Files that can't be cleaned are encrypted but their originals are never shredded.")
                if page.key_mode():
                    notes.append("The trial decrypt uses your key file.")
                if not self.confirm_shred(items, notes, changes, opts, tips):
                    return self.set_status("Cancelled. Nothing was changed.")
                opts.confirmed_changes = {p: list(cs) for p, cs in changes.items()}  # exactly what was shown
                for r in holders:
                    opts.confirmed_changes.setdefault(str(r.path), []).extend(sorted(JPEG_CHANGES))
            paths = [r.path for r in run_rows]
            pw = page.take_credential()
            if not steps.needs_passphrase(paths, opts):
                pw = ""
            self._log = []
            page.set_running(run_rows)
            self._thread = QThread()
            self._worker = _Worker(paths, pw, opts)
            del pw
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.line.connect(self._log.append)
            self._worker.prog.connect(self._progress)
            self._worker.done.connect(self._finished)
            self._run_page, self._run_opts = page, opts
            started = True
            self.runStarted.emit(opts, paths)
            self._thread.start()
            self.set_status(f"Working on 1 of {len(paths)}: {paths[0].name}")
        finally:
            if not started:
                self._set_busy(False)
                page.restore_focus(page.primaryBtn)     # a dialog was cancelled or refused: back to the button pressed
                if self._closing:       # closed while preparing the run: nothing is running, so the window can go now
                    self.close()

    def _progress(self, done: int, total: int) -> None:
        self._run_page.mark_progress(done, total)
        if done < total and not self._closing:
            self.set_status(f"Working on {done + 1} of {total}: {self._run_page._run_rows[done].path.name}")

    def cancel(self) -> None:
        if self._thread is not None and self._worker is not None:
            self._worker.cancel.set()
            self._run_page.cancelBtn.setEnabled(False)
            self.set_status("Cancelling after the current file...")

    def _finished(self, results: list) -> None:
        self._thread.quit(); self._thread.wait()
        self._thread = None; self._worker = None
        page = self._run_page
        page.show_results(results, self._log)
        n_run = len(page._run_rows)
        self.set_status(m.finish_text([d[0].status for d in page._results], n_run))
        self._set_busy(False)
        page.primaryBtn.setEnabled(True)
        page.restore_focus()        # the body is enabled again, so the results table can take focus now
        self.runFinished.emit(results)
        if self._closing:
            self.close()

    def closeEvent(self, event):
        # Closing the window is a cancel: keep the window until the worker has finished its current file
        # (it may be mid-shred), then close from _finished. Age is also killed by the Job object if the process dies.
        if self._thread is not None and self._thread.isRunning():
            self._closing = True
            self._worker.cancel.set()
            self._run_page.cancelBtn.setEnabled(False)
            self.set_status("Closing after the current file...")
            event.ignore()
            return
        for page in self.pages.values():
            page.forget_keys()
        if self.busy:
            # the run is still being prepared (a category scan keeps the window alive): cancel the scan and close once
            # begin_run has stopped, so no worker is started in a window that is going away
            self._closing = True
            self._scan_cancel.set()
            event.ignore()
            return
        for p in self.pages.values():
            p.take_passphrase()
        self._pool.shutdown(wait=False, cancel_futures=True)
        event.accept()


_KEEP: list = []     # windows made by a language switch stay referenced until the program ends


def main() -> int:
    app = QApplication(sys.argv[:1])   # Qt would apply -reverse, -platformpluginpath etc.
    apply_theme(app)
    from .. import i18n
    saved = settings.load().get("language", "system")
    # SHIVA_LANG=en|zh_TW overrides the saved choice, which overrides the OS language; English when no catalog is found
    i18n.install(None if os.environ.get("SHIVA_LANG") or saved == "system" else saved)
    w = MainWindow(); _KEEP.append(w); w.show()
    w.pages[w.current_tab()].add_button().setFocus()   # start on the main action, not on a navigation button
    if "--smoke" in sys.argv:
        print("SMOKE_OK", flush=True)
        return 0
    return app.exec()
