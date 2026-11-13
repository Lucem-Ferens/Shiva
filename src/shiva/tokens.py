"""Design tokens: spacing, type, radii, targets, timing and light/dark colours.

Qt-free except `theme_from_palette`, which only reads a palette object. Values here are the single source for style sheets;
`tests/test_tokens.py` checks the contrast of every pair that is used for text, icons, borders and focus rings.
"""
from __future__ import annotations

from string import Template

SPACE = {1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32}
RADIUS = {"control": 4, "card": 8}
TARGET = {"button_h": 32, "check_h": 24, "radio_h": 28, "menu_btn": 32, "nav_h": 36}
SCROLLBAR_W = 12          # width of the thin scroll bar (handle only, no arrow buttons)
CONTROL_CONTENT_H = 22   # content height inside the 1 px border and 4 px padding: buttons and one-line fields are 32 px tall
FOCUS = {"width": 2, "gap": 2}
NAV_BAR = 3             # width of the accent bar on the selected navigation row
SIDEBAR_W = 184
NARROW_W = 640          # content width below which the Type and Size columns are hidden
COMPACT_H = 520         # content height below which the scope line and the step lists are left out

# name: (px, line px, weight)
TYPE = {
    "caption": (12, 16, 400),
    "body": (14, 20, 400),
    "body-strong": (14, 20, 600),
    "section": (18, 24, 600),
    "title": (20, 28, 600),
}
FONT_FAMILY = '"Segoe UI Variable", "Segoe UI", "Microsoft JhengHei UI", sans-serif'

FEEDBACK_MS = 100       # a visible reaction to a click
BUSY_AFTER_MS = 1000    # show a working indicator after this
LONG_RUN_S = 10         # show N of M and Cancel beyond this
CONFIRM_DELAY_MS = 500  # the destructive button of a confirmation stays disabled this long

COLORS = {
    "light": {
        "text": "#0F172A", "text-secondary": "#475569", "accent": "#0369A1", "success": "#166534",
        "warning": "#92400E", "error": "#B91C1C", "border": "#64748B", "bg": "#F7F8FA", "surface": "#FFFFFF",
        "on-accent": "#FFFFFF", "on-error": "#FFFFFF",
        "side": "#EEF1F6", "surface-strong": "#E8ECF2", "selected": "#DCEBF8", "line": "#E2E8F0", "warning-bg": "#FFFBEB",
        "accent-hover": "#075985", "accent-press": "#0C4A6E", "error-hover": "#991B1B", "surface-hover": "#DDE3EC",
        # decoration only (never for text that carries meaning)
        "disabled-text": "#5F6672",
    },
    "dark": {
        "text": "#E8EDF5", "text-secondary": "#A3B1C6", "accent": "#38BDF8", "success": "#4ADE80",
        "warning": "#FCD34D", "error": "#FCA5A5", "border": "#6B7FA3", "bg": "#0E1420", "surface": "#151D2C",
        "on-accent": "#06223A", "on-error": "#0F172A",
        "side": "#0A0F19", "surface-strong": "#1E2839", "selected": "#1C3350", "line": "#243044", "warning-bg": "#2A2410",
        "accent-hover": "#7DD3FC", "accent-press": "#0EA5E9", "error-hover": "#FECACA", "surface-hover": "#26334A",
        "disabled-text": "#8896AD",
    },
}

# Black layer drawn over the window behind a modal dialog (opacity per theme); not a colour token because it has alpha.
SCRIM_ALPHA = {"light": 0.40, "dark": 0.55}
# Cursor glow on the empty drop card: (rgb, opacity, radius px). Decoration only; text stays above 4.5:1 under it.
SPOTLIGHT = {"light": ((3, 105, 161), 0.06, 200), "dark": ((255, 255, 255), 0.06, 200)}


def _lin(c: int) -> float:
    v = c / 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast(fg: str, bg: str) -> float:
    """WCAG 2 contrast ratio of two #rrggbb colours."""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def theme_from_palette(palette) -> str:
    """'dark' when the window background of a QPalette is dark, else 'light' (follow the system theme)."""
    return "dark" if palette.window().color().lightness() < 128 else "light"


_QSS = Template("""
QWidget#side { background: $side; border-right: 1px solid $line; }
QToolButton#nav { border: 2px solid transparent; border-left: ${nav_bar}px solid transparent; border-radius: ${r_control}px; padding: 0 ${s3}px; background: transparent; }
QToolButton#nav:hover { background: $surface_strong; }
QToolButton#nav:checked { background: $selected; border-left: ${nav_bar}px solid $accent; }
QToolButton#nav QLabel { color: $text_secondary; background: transparent; }
QToolButton#nav:checked QLabel { color: $text; font-weight: 600; }
QToolButton#nav:focus { border: ${f_width}px solid $accent; border-left: ${nav_bar}px solid $accent; }
QLabel#brand { font-size: ${t_section}px; font-weight: 600; padding: ${s1}px ${s3}px ${s4}px ${s3}px; }
QPushButton { border: 1px solid $line; border-radius: ${r_control}px; padding: ${s1}px ${s4}px; background: $surface_strong; color: $text; }
QPushButton:hover { background: $surface_hover; }
QPushButton:pressed { background: $selected; }
QPushButton[kbFocus="true"]:focus { border: ${f_width}px solid $accent; }
QPushButton:disabled { color: $disabled_text; border-color: $line; background: $bg; }
QPushButton[primary="true"] { background: $accent; color: $on_accent; border-color: $accent; font-weight: 600; }
QPushButton[primary="true"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[primary="true"]:pressed { background: $accent_press; border-color: $accent_press; }
QPushButton[primary="true"]:disabled { background: $surface_strong; color: $disabled_text; border-color: $line; }
QPushButton[danger="true"] { background: $error; color: $on_error; border-color: $error; font-weight: 600; }
QPushButton[danger="true"]:hover, QPushButton[danger="true"]:pressed { background: $error_hover; border-color: $error_hover; }
QPushButton[danger="true"]:disabled { background: $surface_strong; color: $disabled_text; border-color: $line; }
QPushButton[primary="true"][kbFocus="true"]:focus { border: ${f_width}px solid $on_accent; }
QPushButton[danger="true"][kbFocus="true"]:focus { border: ${f_width}px solid $on_error; }
QPushButton:flat { border: 0; background: transparent; padding: 0; }
QPushButton, QLineEdit { min-height: ${ctl_h}px; }
QLineEdit, QPlainTextEdit { border: 1px solid $border; border-radius: ${r_control}px; padding: ${s1}px ${s2}px; background: $surface; color: $text; }
QLineEdit:focus, QPlainTextEdit:focus { border: ${f_width}px solid $accent; }
QTableWidget { border: 1px solid $line; border-radius: ${r_card}px; background: $surface; gridline-color: $line; selection-background-color: $selected; selection-color: $text; }
QHeaderView::section { background: $surface_strong; color: $text_secondary; border: 0; border-bottom: 1px solid $line; padding: ${s1}px ${s2}px; font-weight: 600; }
QLabel#keepQualityHint, QLabel#keepPartsHint, QLabel#uncleanHint, QLabel#warningNote { background: $warning_bg; color: $text; border: 1px solid $warning; border-radius: ${r_control}px; padding: ${s2}px ${s3}px; }
QGroupBox { border: 0; margin-top: ${s4}px; padding: 0; font-weight: 600; }
QGroupBox#nextStepsGroup { border: 1px solid $line; border-radius: ${r_card}px; background: $surface; margin-top: ${s5}px; padding: ${s3}px; }
QFrame#optionCard { border: 1px solid $line; border-radius: ${r_card}px; background: $surface; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 0; padding: 0; }
QLabel#confirmTitle { font-size: ${t_section}px; font-weight: 600; }
QListWidget#confirmItems { border: 1px solid $line; border-radius: ${r_card}px; background: $surface; padding: ${s1}px; }
QListWidget#confirmItems:focus { border: ${f_width}px solid $accent; }
QPushButton#learnLink { color: $accent; text-decoration: underline; border: ${f_width}px solid transparent; background: transparent; padding: 0; min-height: 0; text-align: left; }
QPushButton#learnLink[kbFocus="true"]:focus { border: ${f_width}px solid $accent; }
QPushButton#learnLink[indent="true"] { padding-left: ${s5}px; }
QFrame#emptyDrop { border: 1px solid $line; border-radius: ${r_card}px; background: $surface; }
QFrame#emptyDrop QLabel { background: transparent; }
QLabel#emptyHead { font-size: ${t_section}px; font-weight: 600; }
QLabel#emptySub { color: $text_secondary; }
QTableWidget[dropState="ok"], QFrame#emptyDrop[dropState="ok"] { border: 2px dashed $accent; border-radius: ${r_card}px; background: $selected; }
QTableWidget[dropState="refused"], QFrame#emptyDrop[dropState="refused"] { border: 2px dashed $error; border-radius: ${r_card}px; background: $warning_bg; }
QLabel#dropCue { color: $text; font-weight: 600; padding: ${s1}px; }
QToolButton#nav[dropTarget="true"] { background: $surface_strong; border: 2px dashed $accent; }
QLabel#resultBanner { background: $surface; border: 1px solid $line; border-left: ${s1}px solid $border; border-radius: ${r_control}px; padding: ${s2}px ${s3}px; }
QLabel#resultBanner[level="success"] { border-left-color: $success; }
QLabel#resultBanner[level="warning"] { border-left-color: $warning; background: $warning_bg; }
QLabel#resultBanner[level="error"] { border-left-color: $error; }
QLabel#pageTitle { font-size: ${t_title}px; font-weight: 600; }
QLabel#pageScope, QLabel#shredAfterReason { color: $text_secondary; }
QLabel#statusWarning { color: $warning; }
QScrollBar:vertical { background: transparent; width: ${sb}px; margin: 0; }
QScrollBar:horizontal { background: transparent; height: ${sb}px; margin: 0; }
QScrollBar::handle:vertical { background: $border; border-radius: ${r_control}px; min-height: 24px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border; border-radius: ${r_control}px; min-width: 24px; margin: 2px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover, QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed { background: $text_secondary; }
QScrollBar::handle:vertical:disabled, QScrollBar::handle:horizontal:disabled { background: transparent; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QLabel#fieldLabel { font-weight: 600; }
QLabel#statusError { color: $error; font-weight: 600; }
""")


def palette_roles(theme: str) -> dict[str, str]:
    """Qt palette role -> colour for one theme, so native widgets (check boxes, menus, scroll bars) follow the tokens."""
    c = COLORS[theme]
    return {"Window": c["bg"], "WindowText": c["text"], "Base": c["surface"], "AlternateBase": c["surface-strong"],
            "Text": c["text"], "Button": c["surface"], "ButtonText": c["text"], "Highlight": c["selected"],
            "HighlightedText": c["text"], "PlaceholderText": c["text-secondary"], "Link": c["accent"],
            "ToolTipBase": c["surface"], "ToolTipText": c["text"], "Mid": c["border"]}


def stylesheet(theme: str) -> str:
    """Style sheet text for one theme, built only from the tokens above."""
    c = {k.replace("-", "_"): v for k, v in COLORS[theme].items()}
    return _QSS.substitute(c, r_control=RADIUS["control"], r_card=RADIUS["card"], s1=SPACE[1], s2=SPACE[2], s3=SPACE[3],
                           s4=SPACE[4], s5=SPACE[5], f_width=FOCUS["width"], nav_bar=NAV_BAR, ctl_h=CONTROL_CONTENT_H, sb=SCROLLBAR_W, t_section=TYPE["section"][0], t_title=TYPE["title"][0])
