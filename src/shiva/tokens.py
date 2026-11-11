"""Design tokens: spacing, type, radii, targets, timing and light/dark colours.

Qt-free except `theme_from_palette`, which only reads a palette object. Values here are the single source for style sheets;
`tests/test_tokens.py` checks the contrast of every pair that is used for text, icons, borders and focus rings.
"""
from __future__ import annotations

from string import Template

SPACE = {1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32}
RADIUS = {"control": 8, "card": 8}
TARGET = {"button_h": 32, "check_h": 24, "menu_btn": 32, "nav_h": 36}
FOCUS = {"width": 2, "gap": 2}
SIDEBAR_W = 184
NARROW_W = 640          # content width below which the Type and Size columns are hidden

# name: (px, line px, weight)
TYPE = {
    "caption": (12, 16, 400),
    "body": (14, 20, 400),
    "body-strong": (14, 20, 600),
    "section": (18, 24, 600),
    "title": (22, 28, 600),
}
FONT_FAMILY = '"Segoe UI Variable", "Segoe UI", "Microsoft JhengHei UI", sans-serif'

FEEDBACK_MS = 100       # a visible reaction to a click
BUSY_AFTER_MS = 1000    # show a working indicator after this
LONG_RUN_S = 10         # show N of M and Cancel beyond this
CONFIRM_DELAY_MS = 500  # the destructive button of a confirmation stays disabled this long

COLORS = {
    "light": {
        "text": "#020617", "text-secondary": "#475569", "accent": "#0369A1", "success": "#166534",
        "warning": "#92400E", "error": "#B91C1C", "border": "#64748B", "bg": "#F8FAFC", "surface": "#FFFFFF",
        "on-accent": "#FFFFFF", "on-error": "#FFFFFF",
        "side": "#F1F5F9", "surface-strong": "#EEF2F7", "selected": "#E0F2FE", "line": "#DBE3EC", "warning-bg": "#FFFBEB",
        # decoration only (never for text that carries meaning)
        "disabled-text": "#5F6672",
    },
    "dark": {
        "text": "#F1F5F9", "text-secondary": "#A3B1C6", "accent": "#38BDF8", "success": "#4ADE80",
        "warning": "#FCD34D", "error": "#FCA5A5", "border": "#6B7FA3", "bg": "#0F172A", "surface": "#162033",
        "on-accent": "#06223A", "on-error": "#0F172A",
        "side": "#0B1220", "surface-strong": "#1A2740", "selected": "#1B2A45", "line": "#2A3856", "warning-bg": "#2A2410",
        "disabled-text": "#8896AD",
    },
}


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
QToolButton#nav { border: 2px solid transparent; border-radius: ${r_control}px; padding: 0 ${s3}px; background: transparent; }
QToolButton#nav:hover { background: $surface_strong; }
QToolButton#nav:checked { background: $selected; }
QToolButton#nav QLabel { color: $text_secondary; background: transparent; }
QToolButton#nav:checked QLabel { color: $text; font-weight: 600; }
QToolButton#nav:focus { border: ${f_width}px solid $accent; }
QLabel#brand { font-size: ${t_section}px; font-weight: 600; padding: ${s1}px ${s3}px ${s4}px ${s3}px; }
QPushButton { border: 1px solid $border; border-radius: ${r_control}px; padding: ${s1}px ${s4}px; background: $surface; color: $text; }
QPushButton:hover { background: $surface_strong; }
QPushButton:focus { border: ${f_width}px solid $accent; }
QPushButton:disabled { color: $disabled_text; border-color: $line; background: $bg; }
QPushButton[primary="true"] { background: $accent; color: $on_accent; border-color: $accent; font-weight: 600; }
QPushButton[primary="true"]:hover { background: $accent; }
QPushButton[primary="true"]:disabled { background: $surface_strong; color: $disabled_text; border-color: $line; }
QPushButton[danger="true"] { background: $error; color: $on_error; border-color: $error; font-weight: 600; }
QPushButton[danger="true"]:disabled { background: $surface_strong; color: $disabled_text; border-color: $line; }
QPushButton[primary="true"]:focus { border: ${f_width}px solid $on_accent; }
QPushButton[danger="true"]:focus { border: ${f_width}px solid $on_error; }
QPushButton:flat { border: 0; background: transparent; padding: 0; }
QLineEdit, QPlainTextEdit { border: 1px solid $border; border-radius: ${r_control}px; padding: ${s1}px ${s2}px; background: $surface; color: $text; }
QLineEdit:focus, QPlainTextEdit:focus { border: ${f_width}px solid $accent; }
QTableWidget { border: 1px solid $line; border-radius: ${r_card}px; background: $surface; gridline-color: $line; selection-background-color: $selected; selection-color: $text; }
QHeaderView::section { background: $surface_strong; color: $text_secondary; border: 0; border-bottom: 1px solid $line; padding: ${s1}px ${s2}px; font-weight: 600; }
QLabel#keepQualityHint, QLabel#keepPartsHint, QLabel#uncleanHint { background: $warning_bg; color: $text; border: 1px solid $warning; border-radius: ${r_control}px; padding: ${s2}px ${s3}px; }
QGroupBox { border: 0; margin-top: ${s4}px; padding: 0; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 0; padding: 0; }
QLabel#confirmTitle { font-size: ${t_section}px; font-weight: 600; }
QListWidget#confirmItems { border: 1px solid $line; border-radius: ${r_card}px; background: $surface; padding: ${s1}px; }
QListWidget#confirmItems:focus { border: ${f_width}px solid $accent; }
QPushButton#learnLink { color: $accent; text-decoration: underline; border: ${f_width}px solid transparent; background: transparent; padding: 0 ${s1}px; text-align: left; }
QPushButton#learnLink:focus { border: ${f_width}px solid $accent; }
QPushButton#learnLink[indent="true"] { padding-left: ${s5}px; }
QLabel#emptyDrop { border: 1px dashed $border; border-radius: ${r_card}px; padding: ${s5}px; }
QTableWidget[dropState="ok"], QLabel#emptyDrop[dropState="ok"] { border: 2px dashed $accent; border-radius: ${r_card}px; background: $selected; }
QTableWidget[dropState="refused"], QLabel#emptyDrop[dropState="refused"] { border: 2px dashed $error; border-radius: ${r_card}px; background: $warning_bg; }
QLabel#dropCue { color: $text; font-weight: 600; padding: ${s1}px; }
QToolButton#nav[dropTarget="true"] { background: $surface_strong; border: 2px dashed $accent; }
QLabel#pageTitle { font-size: ${t_title}px; font-weight: 600; }
QLabel#pageScope, QLabel#shredAfterReason { color: $text_secondary; }
QLabel#statusWarning { color: $warning; }
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
                           s4=SPACE[4], s5=SPACE[5], f_width=FOCUS["width"], t_section=TYPE["section"][0], t_title=TYPE["title"][0])
