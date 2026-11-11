"""Modal dialogs: confirmation, about-shredding, PDF/Office category choice.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QTextBrowser, QVBoxLayout)

from .. import tokens
from .. import ui_model as m
from ..i18n import dyn, tr, trf
from .widgets import button_icon, status_icon

# ------------------------------------------------------------------ dialogs
class ConfirmDialog(QDialog):
    """Confirmation of an irreversible action. Cancel is the primary, default and initially focused button; the
    do-it button sits on the LEFT, is disabled for CONFIRM_DELAY_MS (click-through protection) and never reacts to
    Enter. Item lines that start with a tab are sub-lines of the item above (a per-item Warning). `trailer` and the
    help link are shown only when asked for. Esc and X cancel."""

    learnRequested = Signal()

    def __init__(self, parent, title: str, body: str, items: list[str], notes: list[str], do_label: str,
                 trailer: str = "", learn_link: str = "", delay_ms: int | None = None,
                 item_tips: list[str] | None = None):
        super().__init__(parent)
        title, body, do_label = dyn(title), dyn(body), dyn(do_label)
        self.setWindowTitle(title)
        self.setModal(True)
        box = QVBoxLayout(self)
        box.setContentsMargins(*(tokens.SPACE[5],) * 4); box.setSpacing(tokens.SPACE[3])
        head = QLabel(title); head.setObjectName("confirmTitle"); head.setWordWrap(True)
        head.setAccessibleName(title)
        box.addWidget(head)
        text = QLabel(body); text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        box.addWidget(text)
        self.itemList = QListWidget(); self.itemList.setObjectName("confirmItems"); self.itemList.setAccessibleName(tr("Items affected"))
        theme = tokens.theme_from_palette(self.palette())
        for i in items:
            if i.startswith("\t"):      # a per-item Warning: the word is in the text, the triangle beside it
                it = QListWidgetItem(dyn(i[1:])); it.setIcon(status_icon("warning", 16, theme))
            else:
                it = QListWidgetItem(dyn(i))
            self.itemList.addItem(it)
        for i, tip in enumerate(item_tips or []):      # full path as tooltip and accessible text (the line may be shortened)
            if tip and i < self.itemList.count():
                it = self.itemList.item(i)
                it.setToolTip(tip); it.setData(Qt.AccessibleTextRole, tip)
        self.itemList.setMaximumHeight(140)             # taller lists scroll
        self.itemList.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # long lines wrap, so no row is cut off
        self.itemList.setWordWrap(True)
        self.setMinimumWidth(480)
        box.addWidget(self.itemList)
        for n in notes:
            lab = QLabel(dyn(n)); lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            box.addWidget(lab)
        self.trailerLabel = QLabel(dyn(trailer)); self.trailerLabel.setWordWrap(True)
        self.trailerLabel.setVisible(bool(trailer))
        box.addWidget(self.trailerLabel)
        self.learnLink = QPushButton(dyn(learn_link)); self.learnLink.setFlat(True); self.learnLink.setCursor(Qt.PointingHandCursor)
        self.learnLink.setAutoDefault(False); self.learnLink.setAccessibleName(dyn(learn_link))
        self.learnLink.setObjectName("learnLink")
        self.learnLink.setVisible(bool(learn_link))
        self.learnLink.clicked.connect(self.learnRequested)
        box.addWidget(self.learnLink)
        row = QHBoxLayout(); row.addStretch(1); row.setSpacing(tokens.SPACE[2])
        self.doBtn = QPushButton(do_label); self.doBtn.setAutoDefault(False); self.doBtn.setProperty("danger", True)
        self.doBtn.setIcon(button_icon("warning", 16, theme))     # the word on the button stays the accessible name
        self.cancelBtn = QPushButton(tr("Cancel")); self.cancelBtn.setDefault(True)
        self.cancelBtn.setProperty("primary", True)
        row.addWidget(self.doBtn); row.addWidget(self.cancelBtn)
        box.addLayout(row)
        self.doBtn.clicked.connect(self.accept)
        self.cancelBtn.clicked.connect(self.reject)
        self.setTabOrder(self.doBtn, self.cancelBtn)
        self.cancelBtn.setFocus()
        delay = tokens.CONFIRM_DELAY_MS if delay_ms is None else delay_ms
        if delay > 0:
            self.doBtn.setEnabled(False)
            self.doBtn.setAccessibleDescription(tr("Available in a moment."))
            QTimer.singleShot(delay, self._enable_do)

    def _enable_do(self) -> None:
        self.doBtn.setEnabled(True)
        self.doBtn.setAccessibleDescription("")


class AboutShreddingDialog(QDialog):
    """Help page: why shredding can fail and what helps. Mechanism words are allowed here only."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("aboutShredding")
        self.setWindowTitle(dyn(m.ABOUT_TITLE))
        self.resize(560, 520)
        box = QVBoxLayout(self)
        self.aboutText = QTextBrowser(); self.aboutText.setAccessibleName(dyn(m.ABOUT_TITLE))
        html, in_list = [], False
        for kind, text in m.ABOUT_SHREDDING:
            esc = dyn(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if in_list and kind != "b":
                html.append("</ul>"); in_list = False
            if kind == "h":
                html.append(f"<h3>{esc}</h3>")
            elif kind == "b":
                if not in_list:
                    html.append("<ul>"); in_list = True
                html.append(f"<li>{esc}</li>")
            else:
                html.append(f"<p>{esc}</p>")
        if in_list:
            html.append("</ul>")
        self.aboutText.setHtml("".join(html))
        box.addWidget(self.aboutText, 1)
        row = QHBoxLayout(); row.addStretch(1)
        self.closeBtn = QPushButton(tr("Close")); self.closeBtn.setDefault(True); self.closeBtn.setProperty("primary", True)
        row.addWidget(self.closeBtn)
        box.addLayout(row)
        self.closeBtn.clicked.connect(self.accept)


class PdfDialog(QDialog):
    """Categories found in one PDF or Office/ODF document (kind "pdf" | "office"). Ticks follow
    default_checked; rows with selectable=False are shown disabled with their reason and are never removed.
    Default button is the non-destructive one."""

    def __init__(self, parent, name: str, cats: list[dict], remaining: int = 0, kind: str = "pdf"):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(trf("Choose what to remove from %1", name))
        box = QVBoxLayout(self)
        n_sel = sum(1 for c in cats if c.get("selectable", True))
        intro = QLabel(trf("Categories found: %1. Ticked ones will be removed.", n_sel))
        intro.setWordWrap(True); box.addWidget(intro)
        self.boxes: list[tuple[str, QCheckBox]] = []
        self.labels: dict[str, str] = {}
        self.changing: set[str] = set()
        for c in cats:
            label = f"{dyn(c['label'])}: {dyn(c['detail'])}" + (("  " + tr("(changes content)")) if c["content_changing"] else "")
            cb = QCheckBox(label); cb.setAccessibleName(label)
            if c.get("selectable", True):
                cb.setChecked(bool(c.get("default_checked", True)))
                self.boxes.append((c["key"], cb))
                self.labels[c["key"]] = c["label"]
                if c["content_changing"]:
                    self.changing.add(c["key"])
                box.addWidget(cb)
            else:
                cb.setChecked(False); cb.setEnabled(False)
                why = QLabel(dyn(c.get("reason", ""))); why.setWordWrap(True); why.setIndent(24)
                cb.setAccessibleDescription(dyn(c.get("reason", "")))
                box.addWidget(cb); box.addWidget(why)
        if kind == "pdf":
            from ..metadata.pdf import pdf_limits
            note = QLabel(tr('Items marked "changes content" alter what the PDF does or holds. Limits:') + "\n- "
                          + "\n- ".join(dyn(x) for x in pdf_limits()))   # limits are shown BEFORE the run, not only in the result
            others = "PDFs"
        else:
            note = QLabel(tr('Items marked "changes content" alter what readers of the document see. '
                             "Unticked items are copied unchanged. Images inside the document are cleaned too."))
            others = "documents"
        note.setWordWrap(True); box.addWidget(note)
        self.flattenBox = dict(self.boxes).get("flatten") if kind == "pdf" else None
        if self.flattenBox is not None:
            # flattening supersedes every other category: the others are greyed out while it is ticked
            self.flattenBox.toggled.connect(self._flatten_toggled)
        # Content-changing removals are not confirmed in a second window (only irreversible actions are); the
        # consequence is stated here, next to the ticks, and updates with them.
        self.warnLabel = QLabel(""); self.warnLabel.setWordWrap(True)
        self.warnLabel.setAccessibleName(tr("Warning about the ticked items"))
        self.warnLabel.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        box.addWidget(self.warnLabel)
        for _k, cb in self.boxes:
            cb.toggled.connect(self._update_warning)
        self._update_warning()
        self.useOthersCheck = QCheckBox(trf(f"Use these choices for all other {others} (%1)", remaining))
        self.useOthersCheck.setVisible(remaining > 0)
        box.addWidget(self.useOthersCheck)
        row = QHBoxLayout(); row.addStretch(1)
        self.okBtn = QPushButton(tr("Use these choices")); self.okBtn.setDefault(True)
        self.cancelBtn = QPushButton(tr("Cancel")); self.cancelBtn.setAutoDefault(False)
        row.addWidget(self.okBtn); row.addWidget(self.cancelBtn)
        box.addLayout(row)
        self.okBtn.clicked.connect(self._ok); self.cancelBtn.clicked.connect(self.reject)

    def chosen(self) -> list[str]:
        return [k for k, cb in self.boxes if cb.isChecked() and cb.isEnabled()]

    def chosen_changes(self) -> list[str]:
        return [self.labels[k] for k in self.chosen() if k in self.changing]

    def _flatten_toggled(self, on: bool):
        for k, cb in self.boxes:
            if k != "flatten":
                cb.setEnabled(not on)
        self._update_warning()

    def warning_text(self) -> str:
        """The inline warning for what is ticked right now ('' when nothing changes content)."""
        if self.flattenBox is not None and self.flattenBox.isChecked():
            return tr("Warning: every page becomes a picture. Text can't be selected or searched, and links, "
                      "bookmarks, form fields and attachments are lost.")
        changes = self.chosen_changes()
        if changes:
            return trf("Warning: the cleaned copy will not contain %1. Readers will no longer see them.",
                       ", ".join(dyn(c) for c in changes))
        return ""

    def _update_warning(self, *_):
        text = self.warning_text()
        self.warnLabel.setText("⚠ " + text if text else ""); self.warnLabel.setVisible(bool(text))

    def _ok(self):
        self.accept()
