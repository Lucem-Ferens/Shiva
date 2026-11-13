"""Small widgets and the theme: screen-reader announcements, drop helpers, navigation buttons, token style.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QRect, QSize, Signal, QPointF, QRectF
from PySide6.QtGui import (QAction, QBrush, QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QProxyStyle, QPushButton, QStyle, QStyleOptionButton,
                               QStyleOptionFocusRect, QSizePolicy, QTableWidget, QToolButton, QVBoxLayout, QWidget)

from .. import tokens
from .. import ui_model as m
from ..i18n import dyn, tr

def _eye_icon(widget: QWidget, crossed: bool) -> QIcon:
    """An eye (crossed out while the password is hidden), drawn in the field's text colour: no image files."""
    pm = QPixmap(32, 32)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    col = QColor(widget.palette().color(QPalette.Text))
    p.setPen(QPen(col, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    lens = QPainterPath()
    lens.moveTo(1.5, 8)
    lens.quadTo(8, 1.0, 14.5, 8)
    lens.quadTo(8, 15.0, 1.5, 8)
    p.drawPath(lens)
    p.drawEllipse(QPointF(8, 8), 2.2, 2.2)
    if crossed:
        p.drawLine(QPointF(2.5, 13.5), QPointF(13.5, 2.5))
    p.end()
    return QIcon(pm)


class Reveal(QObject):
    """The eye button inside one or more password fields. One state for all of them: clicking (or Alt+W in a focused
    field) shows or hides every field's text. Replaces a "Show password" check box beside the field."""
    toggled = Signal(bool)

    def __init__(self, edits):
        super().__init__(edits[0])
        self._edits = list(edits)
        self._on = False
        self._actions: list[QAction] = []
        for e in self._edits:
            a = QAction(e)
            a.setCheckable(True)
            a.setShortcut(QKeySequence("Alt+W"))
            a.setShortcutContext(Qt.WidgetShortcut)
            a.toggled.connect(self.setChecked)
            e.addAction(a, QLineEdit.TrailingPosition)
            self._actions.append(a)
        self._apply()

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, on: bool) -> None:
        changed = bool(on) != self._on
        self._on = bool(on)
        self._apply()
        if changed:
            self.toggled.emit(self._on)

    def _apply(self) -> None:
        text = tr("Hide password") if self._on else tr("Show password")
        for e, a in zip(self._edits, self._actions):
            e.setEchoMode(QLineEdit.Normal if self._on else QLineEdit.Password)
            a.blockSignals(True)
            a.setChecked(self._on)
            a.blockSignals(False)
            a.setIcon(_eye_icon(e, not self._on))
            a.setText(text)
            a.setToolTip(text + " (Alt+W)")


def _announce(widget: QWidget, text: str) -> None:
    """Tell screen readers about a status change (best effort)."""
    widget.setAccessibleDescription(text)
    try:
        from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(widget, text))
    except Exception:
        pass


def _urls(mime) -> list[Path]:
    return [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]


# ------------------------------------------------------------------ small widgets
class _NavIcon(QWidget):
    """A 16 px line icon (no image files): 1.5 px stroke, filled while its page is selected. Unselected it uses the
    secondary text colour; selected the main text colour (the label is bold then, so colour is never the only cue)."""
    STROKE = 1.5

    def __init__(self, tab: str):
        super().__init__()
        self.tab = tab
        self.filled = False
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_filled(self, on: bool) -> None:
        if on != self.filled:
            self.filled = on
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        col = pal.color(QPalette.Text if self.filled or high_contrast_active() else QPalette.PlaceholderText)
        p.setPen(QPen(col, self.STROKE, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        fill = QBrush(col) if self.filled else Qt.NoBrush
        pt = QPointF
        if self.tab == "clean":
            for y, x2 in ((3, 14), (8, 10), (13, 14)):
                p.drawLine(pt(2, y), pt(x2, y))
            if self.filled:
                p.setBrush(fill); p.drawEllipse(pt(13, 8), 1.6, 1.6)
        elif self.tab == "encrypt":
            p.drawArc(5, 1, 6, 8, 0, 180 * 16)
            p.setBrush(fill); p.drawRoundedRect(3, 7, 10, 7, 1, 1)
        elif self.tab == "shred":
            p.drawLine(pt(3, 4), pt(13, 4)); p.drawLine(pt(6, 4), pt(6, 2)); p.drawLine(pt(6, 2), pt(10, 2))
            p.drawLine(pt(10, 2), pt(10, 4))
            if self.filled:
                p.setBrush(fill)
                p.drawPolygon([pt(4.5, 4), pt(5.2, 14), pt(10.8, 14), pt(11.5, 4)])
            else:
                p.drawPolyline([pt(4.5, 4), pt(5.2, 14), pt(10.8, 14), pt(11.5, 4)])
        elif self.tab == "settings":
            p.setBrush(fill)
            for y, x in ((4, 5), (12, 11)):
                p.drawLine(pt(2, y), pt(14, y)); p.drawEllipse(pt(x, y), 2, 2)
        elif self.tab == "about":
            p.setBrush(fill); p.drawEllipse(pt(8, 8), 6, 6)
            if self.filled:
                p.setPen(QPen(pal.color(QPalette.Highlight), self.STROKE, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawLine(pt(8, 7), pt(8, 11.5)); p.drawPoint(pt(8, 4.6))
        p.end()


class DimLayer(QWidget):
    """A translucent black layer over a window while a modal dialog is open (so the page behind recedes). It never takes
    focus or mouse input, follows the window size, and is not drawn in Windows High Contrast (the user's colours stay)."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._host = window
        self.setObjectName("dimLayer")
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        window.installEventFilter(self)
        self.setGeometry(window.rect())
        self.raise_(); self.show()

    def eventFilter(self, obj, e) -> bool:
        if obj is self._host and e.type() == QEvent.Resize:
            self.setGeometry(self._host.rect())
        return False

    def remove(self) -> None:
        self._host.removeEventFilter(self)
        self.hide(); self.deleteLater()

    def paintEvent(self, _e) -> None:
        if high_contrast_active():
            return
        theme = tokens.theme_from_palette(self.palette())
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, round(255 * tokens.SCRIM_ALPHA[theme])))
        p.end()


class OptionCard(QFrame):
    """One rounded surface holding option rows; a hairline separates the rows that are shown. Hidden while no row is
    shown. Which options count is decided by ui_model.option_view, never by this container."""

    def __init__(self):
        super().__init__()
        self.setObjectName("optionCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(tokens.SPACE[3], tokens.SPACE[2], tokens.SPACE[3], tokens.SPACE[2])
        self._lay.setSpacing(tokens.SPACE[4])        # a hairline sits in the middle of this gap: 8 px above and below

    def add_row(self, w: QWidget) -> None:
        self._lay.addWidget(w)

    def _shown_rows(self) -> list[QWidget]:
        rows = [self._lay.itemAt(i).widget() for i in range(self._lay.count())]
        return [w for w in rows if w is not None and not w.isHidden()]

    def sync(self) -> None:
        self.setVisible(bool(self._shown_rows()))

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        rows = self._shown_rows()
        if len(rows) < 2:
            return
        if high_contrast_active():
            col = self.palette().color(QPalette.Mid)
        else:
            col = QColor(tokens.COLORS[tokens.theme_from_palette(self.palette())]["line"])
        p = QPainter(self)
        p.setPen(QPen(col, 1))
        for w in rows[1:]:
            y = w.geometry().top() - self._lay.spacing() // 2
            p.drawLine(1, y, self.width() - 2, y)
        p.end()


class DisclosureRow(QPushButton):
    """A whole row that shows or hides a group of options: a chevron (right while closed, down while open) and a label.
    The entire row is the click target (also Space with the keyboard). It is a checkable button, so `isChecked()`,
    `setChecked()` and `toggled` work as they do for a check box, and a screen reader announces a toggle button."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setObjectName("disclosure")
        self.setCheckable(True); self.setAutoDefault(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._ring = False          # the focus ring shows for keyboard focus only, not after a click

    def focusInEvent(self, e) -> None:
        self._ring = e.reason() != Qt.MouseFocusReason
        super().focusInEvent(e)

    def _label_left(self) -> int:
        st = self.style()
        return st.pixelMetric(QStyle.PM_IndicatorWidth) + st.pixelMetric(QStyle.PM_CheckBoxLabelSpacing)

    def sizeHint(self) -> QSize:
        return QSize(self._label_left() + self.fontMetrics().horizontalAdvance(self.text()) + tokens.SPACE[3],
                     tokens.TARGET["check_h"])

    def minimumSizeHint(self) -> QSize:
        return QSize(self._label_left() + self.fontMetrics().horizontalAdvance("W") * 4, tokens.TARGET["check_h"])

    def paintEvent(self, _e) -> None:
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        hc = high_contrast_active()
        if hc:
            text = sec = pal.color(QPalette.WindowText); hover = None; focus = pal.color(QPalette.Highlight)
        else:
            c = tokens.COLORS[tokens.theme_from_palette(pal)]
            text, sec, hover, focus = QColor(c["text"]), QColor(c["text-secondary"]), QColor(c["surface-strong"]), QColor(c["accent"])
        r = QRectF(self.rect())
        if hover is not None and self.underMouse():
            p.setPen(Qt.NoPen); p.setBrush(hover); p.drawRoundedRect(r, tokens.RADIUS["control"], tokens.RADIUS["control"])
        p.save()
        p.translate(self.style().pixelMetric(QStyle.PM_IndicatorWidth) / 2, r.height() / 2)
        if self.isChecked():
            p.rotate(90)
        p.setPen(QPen(sec, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.setBrush(Qt.NoBrush)
        p.drawPolyline([QPointF(-2.5, -4.5), QPointF(2.5, 0), QPointF(-2.5, 4.5)])
        p.restore()
        p.setPen(text)
        left = self._label_left()
        p.drawText(QRectF(left, 0, r.width() - left, r.height()), int(Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine), self.text())
        if self.hasFocus() and self._ring:
            p.setPen(QPen(focus, tokens.FOCUS["width"])); p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), tokens.RADIUS["control"], tokens.RADIUS["control"])
        p.end()


class EmptyCard(QFrame):
    """The empty-list drop surface: a file icon, a heading, one sentence, and the "Add files" / "Add folder" buttons side
    by side. It is the drag target (`dropState` property, styled by the token sheet as QFrame#emptyDrop).

    While the pointer is over it (and nothing is dragged, the system animation setting is on and High Contrast is off) a
    faint radial glow follows the pointer. The glow is decoration only: it carries no information and no control depends on it.
    """

    def __init__(self):
        super().__init__()
        self.setObjectName("emptyDrop")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._head, self._sub = "", ""
        self._glow: QPoint | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(tokens.SPACE[5], tokens.SPACE[5], tokens.SPACE[5], tokens.SPACE[5])
        lay.setSpacing(tokens.SPACE[1])
        lay.addStretch(1)
        self.iconLabel = QLabel(); self.iconLabel.setAlignment(Qt.AlignCenter)
        self.headLabel = QLabel(); self.headLabel.setObjectName("emptyHead"); self.headLabel.setAlignment(Qt.AlignCenter)
        self.headLabel.setWordWrap(True)
        self.subLabel = QLabel(); self.subLabel.setObjectName("emptySub"); self.subLabel.setAlignment(Qt.AlignCenter)
        self.subLabel.setWordWrap(True)
        for w in (self.iconLabel, self.headLabel, self.subLabel):
            lay.addWidget(w)
        self.addBtn = QPushButton(tr("&Add files...")); self.addBtn.setAccessibleName(tr("Add files"))
        self.addBtn.setProperty("primary", True)
        self.folderBtn = QPushButton(tr("Add &folder...")); self.folderBtn.setAccessibleName(tr("Add folder"))
        row = QHBoxLayout(); row.setSpacing(tokens.SPACE[2])
        row.addStretch(1); row.addWidget(self.addBtn); row.addWidget(self.folderBtn); row.addStretch(1)
        lay.addSpacing(tokens.SPACE[3]); lay.addLayout(row)
        lay.addStretch(1)
        for b in (self.addBtn, self.folderBtn):
            b.setMinimumHeight(tokens.TARGET["button_h"])
        for w in (self.iconLabel, self.headLabel, self.subLabel, self.addBtn, self.folderBtn):
            w.setMouseTracking(True); w.installEventFilter(self)
        self._paint_icon()

    # -- texts: the heading and the sentence (the accessible name is both)
    def set_texts(self, head: str, sub: str) -> None:
        self._head, self._sub = head, sub
        self.headLabel.setText(head); self.subLabel.setText(sub); self.subLabel.setVisible(bool(sub))
        self.setAccessibleName(f"{head}. {sub}" if sub else head)

    def text(self) -> str:
        return f"{self._head}\n{self._sub}" if self._sub else self._head

    def _paint_icon(self) -> None:
        size, scale = 40, 2
        pix = QPixmap(size * scale, size * scale); pix.fill(Qt.transparent); pix.setDevicePixelRatio(scale)
        p = QPainter(pix); p.setRenderHint(QPainter.Antialiasing)
        hc = high_contrast_active()
        p.setPen(QPen(self.palette().color(QPalette.Text if hc else QPalette.PlaceholderText), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        pt = QPointF
        # a down arrow onto a line (a 24-unit drawing scaled to 40): "drop it here"
        p.drawLine(pt(20, 7), pt(20, 25))
        p.drawPolyline([pt(11.7, 18.3), pt(20, 26.7), pt(28.3, 18.3)])
        p.drawLine(pt(8.3, 33.3), pt(31.7, 33.3))
        p.end()
        self.iconLabel.setPixmap(pix)

    def changeEvent(self, e) -> None:
        if e.type() in (QEvent.PaletteChange, QEvent.StyleChange) and hasattr(self, "iconLabel"):
            self._paint_icon()
        super().changeEvent(e)

    # -- cursor glow
    def glow_allowed(self) -> bool:
        return (not self.property("dropState") and self.isEnabled() and not high_contrast_active()
                and animations_enabled())

    def glow_pos(self) -> QPoint | None:
        return self._glow if self.glow_allowed() else None

    def _track(self, pos: QPoint) -> None:
        self._glow = pos if self.glow_allowed() else None
        self.update()

    def mouseMoveEvent(self, e) -> None:
        self._track(e.position().toPoint())
        super().mouseMoveEvent(e)

    def eventFilter(self, obj, e) -> bool:
        if e.type() == QEvent.MouseMove and isinstance(obj, QWidget):
            self._track(obj.mapTo(self, e.position().toPoint()))
        return False

    def enterEvent(self, e) -> None:
        self._track(self.mapFromGlobal(self.cursor().pos()))
        super().enterEvent(e)

    def leaveEvent(self, e) -> None:
        # the pointer may only have moved onto a child; leave the glow while it is still inside this card
        if not self.rect().contains(self.mapFromGlobal(self.cursor().pos())):
            self._glow = None; self.update()
        super().leaveEvent(e)

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        pos = self.glow_pos()
        if pos is None:
            return
        theme = tokens.theme_from_palette(self.palette())
        (r, g, b), alpha, radius = tokens.SPOTLIGHT[theme]
        grad = QRadialGradient(QPointF(pos), radius)
        grad.setColorAt(0.0, QColor(r, g, b, round(255 * alpha)))
        grad.setColorAt(1.0, QColor(r, g, b, 0))
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), tokens.RADIUS["card"], tokens.RADIUS["card"])
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setClipPath(path)
        p.fillRect(self.rect(), QBrush(grad))
        p.end()


class NavButton(QToolButton):
    """Checkable sidebar item: a drawn icon and the title; the scope line is the tooltip and part of the accessible
    name. Accepts file drops."""
    dropped = Signal(list)

    def __init__(self, tab: str):
        super().__init__()
        title, sub = m.TITLES[tab]
        self.tab = tab
        self.setObjectName("nav"); self.setCheckable(True); self.setAutoExclusive(True)
        self.setFocusPolicy(Qt.NoFocus)    # TabFocus while checked: the sidebar is one tab stop, on the selected item
        self.toggled.connect(self._roving)
        self.setAccessibleName(f"{dyn(title)}, {dyn(sub)}")
        self.setToolTip(dyn(sub))
        self.setFixedHeight(tokens.TARGET["nav_h"])
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAcceptDrops(True)
        lay = QHBoxLayout(self); lay.setContentsMargins(tokens.SPACE[3], 0, tokens.SPACE[3], 0); lay.setSpacing(tokens.SPACE[3])
        icon = self.icon_widget = _NavIcon(tab)
        a = QLabel(dyn(title))
        for w in (icon, a):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(icon); lay.addWidget(a, 1)

    def _roving(self, on: bool) -> None:
        """Only the selected item takes Tab (focus ring only for keyboard users); the arrow keys still move between the
        items and select them. The description says which item is the current page."""
        self.setFocusPolicy(Qt.TabFocus if on else Qt.NoFocus)
        self.icon_widget.set_filled(on)
        self.setAccessibleDescription(tr("Current page") if on else "")

    def _set_target(self, on: bool) -> None:
        if bool(self.property("dropTarget")) == on:
            return
        self.setProperty("dropTarget", on)
        if high_contrast_active():
            self.setStyleSheet("QToolButton#nav { border: 2px dashed palette(highlight); }" if on else "")
        self.style().unpolish(self); self.style().polish(self); self.update()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self._set_target(True)
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dragLeaveEvent(self, e):
        self._set_target(False)

    def dropEvent(self, e):
        self._set_target(False)
        self.dropped.emit(_urls(e.mimeData()))
        e.acceptProposedAction()


class WrapCheckBox(QCheckBox):
    """A check box whose label wraps onto several lines when the width is short (a plain QCheckBox clips it).
    The text, accessible name and signals are those of QCheckBox."""
    _FLAGS = Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        pol = QSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        pol.setHeightForWidth(True)
        self.setSizePolicy(pol)

    def setText(self, text: str) -> None:
        super().setText(text)
        self.updateGeometry()

    def setDescription(self, text: str) -> None:
        """A second line in secondary text under the label: what the option does or costs. Also the accessible description."""
        self._desc = text
        self.setAccessibleDescription(text)
        self.updateGeometry(); self.update()

    _desc = ""
    _DESC_GAP, _DESC_BOTTOM = 2, 6

    def _label_left(self) -> int:
        st = self.style()
        return st.pixelMetric(QStyle.PM_IndicatorWidth) + st.pixelMetric(QStyle.PM_CheckBoxLabelSpacing)

    def _line_h(self, text: str, w: int) -> int:
        return self.fontMetrics().boundingRect(QRect(0, 0, max(1, w), 10000), int(self._FLAGS), text).height()

    def _text_top(self) -> int:
        """Top of the first text line: the line is centred in a one-row box, the same centre line as the indicator."""
        return max(0, (tokens.TARGET["check_h"] - self.fontMetrics().height()) // 2 - 1)   # 1 px up: Latin and CJK ink centres differ

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        avail = w - self._label_left()
        h = self._line_h(self.text(), avail)
        if self._desc:
            return max(tokens.TARGET["check_h"], self._text_top() + h + self._DESC_GAP + self._line_h(self._desc, avail)
                       + self._DESC_BOTTOM)
        return max(tokens.TARGET["check_h"], h + 2 * self._text_top())

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = self._label_left() + max(fm.horizontalAdvance(self.text()), fm.horizontalAdvance(self._desc))
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:
        return QSize(self._label_left() + self.fontMetrics().horizontalAdvance("W") * 8, tokens.TARGET["check_h"])

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        opt = QStyleOptionButton(); self.initStyleOption(opt)
        st = self.style()
        text = opt.text
        opt.text = ""
        ind = st.subElementRect(QStyle.SE_CheckBoxIndicator, opt, self)
        ind.moveTop((tokens.TARGET["check_h"] - ind.height()) // 2)       # beside the first line, also when the label wraps
        opt.rect = ind
        st.drawPrimitive(QStyle.PE_IndicatorCheckBox, opt, p, self)
        left = self._label_left()
        top = self._text_top()
        trect = QRect(left, top, self.width() - left, self.height() - top)
        st.drawItemText(p, trect, int(self._FLAGS), opt.palette, bool(opt.state & QStyle.State_Enabled), text, QPalette.WindowText)
        if self._desc:
            drect = QRect(left, top + self._line_h(text, self.width() - left) + self._DESC_GAP, self.width() - left, self.height())
            st.drawItemText(p, drect, int(self._FLAGS), opt.palette, bool(opt.state & QStyle.State_Enabled), self._desc,
                            QPalette.Text if high_contrast_active() else QPalette.PlaceholderText)
        if self.hasFocus():
            f = QStyleOptionFocusRect(); f.initFrom(self); f.rect = trect
            st.drawPrimitive(QStyle.PE_FrameFocusRect, f, p, self)
        p.end()


class _Table(QTableWidget):
    deleteRequested = Signal()
    copyRequested = Signal()

    def __init__(self, *args):
        super().__init__(*args)
        self.setTabKeyNavigation(False)    # Tab leaves the table (the default walks cell by cell and never lets go)

    _HINT_ROWS = 5      # the table asks for this many rows at most; it grows into spare height up to its content

    def contentHeight(self) -> int:
        return self.horizontalHeader().height() + sum(self.rowHeight(i) for i in range(self.rowCount())) + 2 * self.frameWidth()

    def sizeHint(self):
        n = min(self.rowCount(), self._HINT_ROWS)
        h = self.horizontalHeader().height() + sum(self.rowHeight(i) for i in range(n)) + 2 * self.frameWidth()
        return QSize(super().sizeHint().width(), max(h, 100))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self.deleteRequested.emit()
        elif e.matches(QKeySequence.Copy):
            self.copyRequested.emit()
        else:
            super().keyPressEvent(e)


class _TokenStyle(QProxyStyle):
    """Fusion with check boxes and radio buttons drawn in the token colours (border, accent fill, check mark)."""

    def __init__(self, theme: str):
        super().__init__("Fusion")
        self._c = tokens.COLORS[theme]

    def polish(self, arg):
        super().polish(arg)
        if isinstance(arg, QPushButton) and not getattr(arg, "_focus_visible_hooked", False):
            arg._focus_visible_hooked = True
            arg.installEventFilter(FOCUS_VISIBLE)      # only the buttons are watched, not every event of the application

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PE_FrameFocusRect and not isinstance(widget, (QPushButton, QToolButton)):
            # check boxes, radios, table cells: the buttons' ring (accent, focus width); shown for keyboard focus only
            if option.state & QStyle.State_KeyboardFocusChange:
                painter.save()
                painter.setRenderHint(QPainter.Antialiasing)
                painter.setPen(QPen(QColor(self._c["accent"]), tokens.FOCUS["width"]))
                painter.setBrush(Qt.NoBrush)
                painter.drawRoundedRect(QRectF(option.rect).adjusted(1, 1, -1, -1), 3, 3)
                painter.restore()
            return
        if element not in (QStyle.PE_IndicatorCheckBox, QStyle.PE_IndicatorRadioButton):
            return super().drawPrimitive(element, option, painter, widget)
        c = self._c
        on = bool(option.state & QStyle.State_On)
        enabled = bool(option.state & QStyle.State_Enabled)
        radio = element == QStyle.PE_IndicatorRadioButton
        r = QRectF(option.rect).adjusted(1.5, 1.5, -1.5, -1.5)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        border = QColor(c["accent"] if on and enabled else c["border"] if enabled else c["line"])
        painter.setPen(QPen(border, 1.5))
        painter.setBrush(QColor(c["accent"] if on and enabled else c["surface"] if enabled else c["surface-strong"]))
        if radio:
            painter.drawEllipse(r)
        else:
            painter.drawRoundedRect(r, 2.5, 2.5)
        if on:
            mark = QColor(c["on-accent"] if enabled else c["disabled-text"])
            if radio:
                painter.setPen(Qt.NoPen); painter.setBrush(mark)
                painter.drawEllipse(r.center(), r.width() / 6, r.width() / 6)
            else:
                painter.setPen(QPen(mark, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                x, y, w, h = r.x(), r.y(), r.width(), r.height()
                painter.drawLine(QPointF(x + w * .25, y + h * .52), QPointF(x + w * .43, y + h * .70))
                painter.drawLine(QPointF(x + w * .43, y + h * .70), QPointF(x + w * .76, y + h * .30))
        painter.restore()


_HC_LINK_CSS = ("QPushButton#learnLink { text-decoration: underline; border: 2px solid transparent; text-align: left; "
                "background: transparent; padding: 0 4px; } QPushButton#learnLink:focus { border: 2px solid palette(highlight); }")


def link_button(b: QPushButton, indent: bool = False) -> QPushButton:
    """Style a flat button as a link: the token sheet styles #learnLink; in Windows High Contrast (no token sheet) the
    system palette is used instead."""
    b.setObjectName("learnLink"); b.setFlat(True); b.setAutoDefault(False)
    b.setCursor(Qt.PointingHandCursor)
    if indent:
        b.setProperty("indent", True)
    if high_contrast_active():
        b.setStyleSheet(_HC_LINK_CSS + ("QPushButton#learnLink { padding-left: 24px; }" if indent else ""))
    return b


def high_contrast_active() -> bool:
    """True when Windows High Contrast is on (the user's own colours must then be left alone)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        class HIGHCONTRASTW(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwFlags", ctypes.c_uint), ("lpszDefaultScheme", ctypes.c_void_p)]
        hc = HIGHCONTRASTW(ctypes.sizeof(HIGHCONTRASTW), 0, None)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x0042, hc.cbSize, ctypes.byref(hc), 0)   # SPI_GETHIGHCONTRAST
        return bool(ok and hc.dwFlags & 1)                                                       # HCF_HIGHCONTRASTON
    except Exception:
        return False


def animations_enabled() -> bool:
    """The Windows "Animation effects" setting (client area animation); True where it cannot be read."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        on = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)   # SPI_GETCLIENTAREAANIMATION
        return bool(on.value) if ok else True
    except Exception:
        return True


class _FocusVisible(QObject):
    """A push button draws its focus ring when the keyboard moved the focus (Tab, Shift+Tab, a shortcut), or when the focus
    arrived by itself (a window opened, a window was re-activated) while the last input was the keyboard. After a mouse
    click, or a window opened by a click, there is no ring: a filled default button then keeps the size of its neighbours."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keyboard = True        # nothing was pressed yet: show the ring on the first focused button

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t == QEvent.KeyPress:
            self.keyboard = True
        elif t in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            self.keyboard = False
        if isinstance(obj, QPushButton) and t in (QEvent.FocusIn, QEvent.FocusOut):
            r = ev.reason()
            kb = t == QEvent.FocusIn and (r in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
                                          or (self.keyboard and r in (Qt.ActiveWindowFocusReason, Qt.PopupFocusReason,
                                                                      Qt.OtherFocusReason)))
            if bool(obj.property("kbFocus")) != kb:
                obj.setProperty("kbFocus", kb)
                obj.style().unpolish(obj); obj.style().polish(obj); obj.update()
        return False


FOCUS_VISIBLE = _FocusVisible()


def apply_theme(app: QApplication) -> str:
    """Fusion style, token palette and token style sheet for the system's light or dark theme. Returns the theme,
    or "contrast" (nothing applied: the system colours stay) when Windows High Contrast is on."""
    if high_contrast_active():
        return "contrast"
    theme = tokens.theme_from_palette(app.palette())
    app.setStyle(_TokenStyle(theme))
    pal = QPalette(app.palette())
    for role, hex_color in tokens.palette_roles(theme).items():
        pal.setColor(getattr(QPalette.ColorRole, role), QColor(hex_color))
    off = QColor(tokens.COLORS[theme]["disabled-text"])
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, off)
    app.setPalette(pal)
    app.setStyleSheet(tokens.stylesheet(theme))
    font = app.font(); font.setFamilies(["Segoe UI Variable", "Segoe UI", "Microsoft JhengHei UI"]); app.setFont(font)
    return theme


_KIND_ROLE = {"ok": "success", "warning": "warning", "error": "error", "working": "accent",
              "skipped": "text-secondary", "cancelled": "text-secondary", "pending": "text-secondary",
              "ready": "text-secondary"}


def _draw_status(p: QPainter, kind: str, s: float, color: QColor) -> None:
    pen = QPen(color, max(1.5, s / 9), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen); p.setBrush(Qt.NoBrush)
    r = QRectF(s * .09, s * .09, s * .82, s * .82)
    pt = lambda x, y: QPointF(s * x, s * y)
    if kind == "warning":                                   # triangle with a bar and a dot
        p.drawPolygon([pt(.5, .08), pt(.94, .90), pt(.06, .90)])
        p.drawLine(pt(.5, .38), pt(.5, .62)); p.drawPoint(pt(.5, .76))
        return
    if kind == "ready":                                     # a small dot: an empty circle would look like an unticked radio button
        p.setPen(Qt.NoPen); p.setBrush(color); p.drawEllipse(QPointF(s * .5, s * .5), s * .11, s * .11)
        return
    p.drawEllipse(r)
    if kind == "ok":                                        # check in a circle
        p.drawPolyline([pt(.28, .52), pt(.44, .67), pt(.72, .35)])
    elif kind == "error":                                   # cross in a circle
        p.drawLine(pt(.34, .34), pt(.66, .66)); p.drawLine(pt(.66, .34), pt(.34, .66))
    elif kind == "skipped":                                 # dash in a circle
        p.drawLine(pt(.30, .5), pt(.70, .5))
    elif kind == "cancelled":                               # slash in a circle
        p.drawLine(pt(.30, .70), pt(.70, .30))
    elif kind == "working":                                 # half-filled circle
        p.setBrush(color); p.drawPie(r, 90 * 16, -180 * 16)
    elif kind == "pending":                                 # clock hands
        p.drawPolyline([pt(.5, .28), pt(.5, .5), pt(.66, .6)])


def status_icon(kind: str, size: int = 16, theme: str = "light", high_contrast: bool | None = None,
                palette: QPalette | None = None) -> QIcon:
    """An icon whose SHAPE tells the kind (ok, warning, error, skipped, cancelled, working, pending, ready), so colour is
    never the only cue. Normal colours come from the tokens (success, warning, error ...); the Selected mode uses the
    selected-row text colour. In Windows High Contrast the system palette (Text / HighlightedText) is used instead."""
    hc = high_contrast_active() if high_contrast is None else high_contrast
    pal = palette or QApplication.palette()
    c = tokens.COLORS[theme]
    normal = pal.color(QPalette.Text) if hc else QColor(c[_KIND_ROLE.get(kind, "text-secondary")])
    selected = pal.color(QPalette.HighlightedText) if hc else QColor(c["text"])
    icon = QIcon()
    for mode, color in ((QIcon.Normal, normal), (QIcon.Selected, selected)):
        scale = 2
        pix = QPixmap(size * scale, size * scale); pix.fill(Qt.transparent); pix.setDevicePixelRatio(scale)
        p = QPainter(pix); p.setRenderHint(QPainter.Antialiasing)
        _draw_status(p, kind, size, color)
        p.end()
        icon.addPixmap(pix, mode)
    return icon


def button_icon(kind: str, size: int = 16, theme: str = "light", role: str = "on-error", high_contrast: bool | None = None,
                palette: QPalette | None = None) -> QIcon:
    """A status shape for the inside of a filled button, drawn in the text colour the style sheet gives that button
    (`role`, on-error for the red one; at least 3:1 on its fill, see tests/test_tokens.py) and in the disabled text
    colour while the button is disabled. In Windows High Contrast the system button text colour is used."""
    hc = high_contrast_active() if high_contrast is None else high_contrast
    pal = palette or QApplication.palette()
    c = tokens.COLORS[theme]
    on = pal.color(QPalette.ButtonText) if hc else QColor(c[role])
    off = pal.color(QPalette.Disabled, QPalette.ButtonText) if hc else QColor(c["disabled-text"])
    icon = QIcon()
    for mode, color in ((QIcon.Normal, on), (QIcon.Disabled, off)):
        scale = 2
        pix = QPixmap(size * scale, size * scale); pix.fill(Qt.transparent); pix.setDevicePixelRatio(scale)
        p = QPainter(pix); p.setRenderHint(QPainter.Antialiasing)
        _draw_status(p, kind, size, color)
        p.end()
        icon.addPixmap(pix, mode)
    return icon


def status_item(item, status: str, theme: str) -> None:
    """Put the status icon beside the status word of a table item (the word stays the text and the accessible name)."""
    kind = m.STATUS_KIND.get(status)
    if kind:
        item.setIcon(status_icon(kind, 16, theme))
