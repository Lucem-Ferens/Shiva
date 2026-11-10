import re
from pathlib import Path

from shiva import tokens as t

TEXT_ROLES = ("text", "text-secondary", "accent", "success", "warning", "error")
GUI = Path(__file__).resolve().parents[1] / "src" / "shiva" / "gui.py"
# Literal ratchet: the token sweep takes this to zero. It may only fall.
GUI_LITERAL_LIMIT = 14


def test_themes_define_the_same_keys():
    assert set(t.COLORS["light"]) == set(t.COLORS["dark"])


def test_text_and_status_roles_pass_4_5_on_bg_and_surface():
    for theme, c in t.COLORS.items():
        for role in TEXT_ROLES:
            for ground in ("bg", "surface"):
                assert t.contrast(c[role], c[ground]) >= 4.5, (theme, role, ground)


def test_border_and_icon_pairs_pass_3():
    for theme, c in t.COLORS.items():
        for role in ("border", "accent", "success", "warning", "error"):
            for ground in ("bg", "surface"):
                assert t.contrast(c[role], c[ground]) >= 3.0, (theme, role, ground)


def test_filled_buttons_and_focus_ring():
    for theme, c in t.COLORS.items():
        assert t.contrast(c["on-accent"], c["accent"]) >= 4.5, theme
        assert t.contrast(c["on-error"], c["error"]) >= 4.5, theme
        for ground in ("bg", "surface"):
            assert t.contrast(c["text"], c[ground]) >= 3.0     # the focus ring is drawn in the text colour


def test_disabled_text_passes_where_it_could_carry_meaning():
    for theme, c in t.COLORS.items():
        for ground in ("bg", "surface"):
            assert t.contrast(c["disabled-text"], c[ground]) >= 4.5, (theme, ground)


def test_contrast_function_known_values():
    assert round(t.contrast("#000000", "#FFFFFF"), 1) == 21.0
    assert round(t.contrast("#FFFFFF", "#FFFFFF"), 1) == 1.0


def test_type_and_spacing_scales():
    assert list(t.TYPE) == ["caption", "body", "body-strong", "section", "title"]
    assert sorted({v[2] for v in t.TYPE.values()}) == [400, 600]
    assert list(t.SPACE.values()) == [4, 8, 12, 16, 24, 32]
    assert set(t.RADIUS.values()) <= {0, 4, 8}
    assert "Microsoft JhengHei UI" in t.FONT_FAMILY


def test_stylesheet_uses_only_token_colours():
    for theme in ("light", "dark"):
        qss = t.stylesheet(theme)
        found = set(re.findall(r"#[0-9A-Fa-f]{6}", qss))
        assert found <= set(t.COLORS[theme].values())


def _literal_count() -> int:
    s = GUI.read_text(encoding="utf-8")
    pats = (r"#[0-9a-fA-F]{6}", r"rgb\(", r"font-size", r"setPointSize", r"setContentsMargins\(", r"font-weight: bold")
    return sum(len(re.findall(p, s)) for p in pats)


def test_gui_literals_do_not_grow():
    assert _literal_count() <= GUI_LITERAL_LIMIT, _literal_count()


def test_layout_roles_have_readable_text_and_visible_controls():
    for theme, c in t.COLORS.items():
        for role in ("text", "text-secondary", "accent"):
            for ground in ("side", "surface-strong", "selected"):
                assert t.contrast(c[role], c[ground]) >= 4.5, (theme, role, ground)
        for role in ("text", "warning"):
            assert t.contrast(c[role], c["warning-bg"]) >= 4.5, (theme, role)
        for ground in ("bg", "surface", "selected", "side", "warning-bg"):
            assert t.contrast(c["border"], c[ground]) >= 3.0, (theme, "border", ground)


def test_focus_ring_differs_from_the_fill_of_filled_buttons():
    for theme, c in t.COLORS.items():
        qss = t.stylesheet(theme)
        assert 'QPushButton[primary="true"]:focus { border: 2px solid ' + c["on-accent"] in qss
        assert 'QPushButton[danger="true"]:focus { border: 2px solid ' + c["on-error"] in qss
        assert t.contrast(c["on-accent"], c["accent"]) >= 3.0 and t.contrast(c["on-error"], c["error"]) >= 3.0, theme
