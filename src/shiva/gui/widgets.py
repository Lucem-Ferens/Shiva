"""Small widgets and the theme: screen-reader announcements, drop helpers, navigation buttons, token style.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QRect, QSize, Signal, QPointF, QRectF
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QProxyStyle, QPushButton, QStyle, QStyleOptionButton,
                               QStyleOptionFocusRect, QSizePolicy, QTableWidget, QToolButton, QWidget)

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
    """A 16 px line icon drawn with the text colour (no image files)."""

    def __init__(self, tab: str):
        super().__init__()
        self.tab = tab
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = self.palette().color(QPalette.Text)
        p.setPen(QPen(col, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        pt = QPointF
        if self.tab == "clean":
            for y, x2 in ((3, 14), (8, 10), (13, 14)):
                p.drawLine(pt(2, y), pt(x2, y))
        elif self.tab == "encrypt":
            p.drawRoundedRect(3, 7, 10, 7, 1, 1)
            p.drawArc(5, 1, 6, 8, 0, 180 * 16)
        elif self.tab == "shred":
            p.drawLine(pt(3, 4), pt(13, 4)); p.drawLine(pt(6, 4), pt(6, 2)); p.drawLine(pt(6, 2), pt(10, 2))
            p.drawLine(pt(10, 2), pt(10, 4)); p.drawLine(pt(4.5, 4), pt(5.2, 14)); p.drawLine(pt(5.2, 14), pt(10.8, 14))
            p.drawLine(pt(10.8, 14), pt(11.5, 4))
        elif self.tab == "settings":
            for y, x in ((4, 5), (12, 11)):
                p.drawLine(pt(2, y), pt(14, y)); p.drawEllipse(pt(x, y), 2, 2)
        elif self.tab == "about":
            p.drawEllipse(pt(8, 8), 6, 6); p.drawLine(pt(8, 7), pt(8, 11.5)); p.drawPoint(pt(8, 4.6))
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
        icon = _NavIcon(tab)
        a = QLabel(dyn(title))
        for w in (icon, a):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(icon); lay.addWidget(a, 1)

    def _roving(self, on: bool) -> None:
        """Only the selected item takes Tab (focus ring only for keyboard users); the arrow keys still move between the
        items and select them. The description says which item is the current page."""
        self.setFocusPolicy(Qt.TabFocus if on else Qt.NoFocus)
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

    def _label_left(self) -> int:
        st = self.style()
        return st.pixelMetric(QStyle.PM_IndicatorWidth) + st.pixelMetric(QStyle.PM_CheckBoxLabelSpacing)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        r = self.fontMetrics().boundingRect(QRect(0, 0, max(1, w - self._label_left()), 10000), int(self._FLAGS), self.text())
        return max(tokens.TARGET["check_h"], r.height() + tokens.SPACE[1])

    def sizeHint(self) -> QSize:
        w = self._label_left() + self.fontMetrics().horizontalAdvance(self.text())
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:
        return QSize(self._label_left() + self.fontMetrics().horizontalAdvance("W") * 8, tokens.TARGET["check_h"])

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        opt = QStyleOptionButton(); self.initStyleOption(opt)
        st = self.style()
        text = opt.text
        opt.text = ""
        opt.rect = st.subElementRect(QStyle.SE_CheckBoxIndicator, opt, self)
        st.drawPrimitive(QStyle.PE_IndicatorCheckBox, opt, p, self)
        left = self._label_left()
        trect = QRect(left, 0, self.width() - left, self.height())
        st.drawItemText(p, trect, int(self._FLAGS), opt.palette, bool(opt.state & QStyle.State_Enabled), text, QPalette.WindowText)
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
    # "ready": plain empty circle


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
