"""Design tokens: spacing, type, radii, targets, timing and light/dark colours.

Qt-free except `theme_from_palette`, which only reads a palette object. Values here are the single source for style sheets;
`tests/test_tokens.py` checks the contrast of every pair that is used for text, icons, borders and focus rings.
"""
from __future__ import annotations

from string import Template

SPACE = {1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32}
RADIUS = {"control": 4, "card": 8}
TARGET = {"button_h": 32, "check_h": 24, "menu_btn": 32}
FOCUS = {"width": 2, "gap": 2}

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
        "text": "#1A1D21", "text-secondary": "#4A5260", "accent": "#0B5CC4", "success": "#0F7A52",
        "warning": "#8A5300", "error": "#B3261E", "border": "#7A828F", "bg": "#FFFFFF", "surface": "#F4F5F7",
        "on-accent": "#FFFFFF", "on-error": "#FFFFFF",
        # decoration only (never for text that carries meaning): the principles' #6B7280 is 4.43:1 on surface
        "disabled-text": "#5F6672",
    },
    "dark": {
        "text": "#ECEEF1", "text-secondary": "#B4BAC4", "accent": "#7FB2FF", "success": "#4FC48F",
        "warning": "#F2B950", "error": "#FF8F87", "border": "#7A828F", "bg": "#1C1F24", "surface": "#262A31",
        "on-accent": "#1C1F24", "on-error": "#1C1F24",
        "disabled-text": "#939AA6",
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
QToolButton#nav { border: 1px solid $border; border-radius: ${r_control}px; padding: ${s1}px ${s3}px; }
QToolButton#nav:checked { border: 2px solid $accent; background: $accent; }
QToolButton#nav:checked QLabel { color: $on_accent; }
QToolButton#nav:focus { border: ${f_width}px dashed $text; }
QLabel#statusWarning { color: $warning; }
QLabel#statusError { color: $error; font-weight: 600; }
""")


def stylesheet(theme: str) -> str:
    """Style sheet text for one theme, built only from the tokens above."""
    c = {k.replace("-", "_"): v for k, v in COLORS[theme].items()}
    return _QSS.substitute(c, r_control=RADIUS["control"], s1=SPACE[1], s3=SPACE[3], f_width=FOCUS["width"])
