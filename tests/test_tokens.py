import re
from pathlib import Path

from shiva import tokens as t

TEXT_ROLES = ("text", "text-secondary", "accent", "success", "warning", "error")
GUI_DIR = Path(__file__).resolve().parents[1] / "src" / "shiva" / "gui"
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
    files = sorted(GUI_DIR.glob("*.py"))
    assert len(files) >= 5
    s = "\n".join(f.read_text(encoding="utf-8") for f in files)
    pats = (r"#[0-9a-fA-F]{6}", r"rgb\(", r"font-size", r"setPointSize", r"setContentsMargins\(\s*[1-9]\d*", r"font-weight: bold")
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


def _blend(fg: str, bg: str, alpha: float) -> str:
    f = [int(fg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x * alpha + y * (1 - alpha)):02X}" for x, y in zip(f, b))


def test_button_hover_and_pressed_steps_keep_their_labels_readable():
    for theme, c in t.COLORS.items():
        for fill in ("accent-hover", "accent-press"):
            assert t.contrast(c["on-accent"], c[fill]) >= 4.5, (theme, fill)
        assert t.contrast(c["on-error"], c["error-hover"]) >= 4.5, theme
        for fill in ("surface-hover", "selected", "surface-strong"):
            for role in ("text", "text-secondary"):
                assert t.contrast(c[role], c[fill]) >= 4.5, (theme, role, fill)
        assert t.contrast(c["accent"], c["surface-hover"]) >= 4.5, theme      # a link or ring on a hovered row
        assert t.contrast(c["disabled-text"], c["surface-strong"]) >= 4.5, theme


def test_borders_stay_visible_on_selected_and_hover_fills():
    for theme, c in t.COLORS.items():
        for ground in ("selected", "surface-strong", "surface-hover"):
            assert t.contrast(c["border"], c[ground]) >= 3.0, (theme, ground)


def test_text_stays_readable_under_the_strongest_cursor_glow():
    for theme, c in t.COLORS.items():
        rgb, alpha, radius = t.SPOTLIGHT[theme]
        glow = "#%02X%02X%02X" % rgb
        card = _blend(glow, c["surface"], alpha)           # worst case: full opacity under the pointer
        for role in ("text", "text-secondary", "accent"):
            assert t.contrast(c[role], card) >= 4.5, (theme, role)
        assert t.contrast(c["border"], card) >= 3.0, (theme, "border")
        assert 0 < alpha <= 0.16 and radius > 0


def test_scrim_is_a_dark_layer_with_a_sane_opacity():
    assert set(t.SCRIM_ALPHA) == {"light", "dark"}
    assert all(0.3 <= a <= 0.7 for a in t.SCRIM_ALPHA.values())


def test_navigation_bar_width_and_radii():
    assert t.NAV_BAR == 3
    assert t.RADIUS == {"control": 4, "card": 8}


def test_stylesheet_has_hover_and_pressed_steps():
    for theme, c in t.COLORS.items():
        qss = t.stylesheet(theme)
        assert 'QPushButton[primary="true"]:hover { background: ' + c["accent-hover"] in qss
        assert 'QPushButton[primary="true"]:pressed { background: ' + c["accent-press"] in qss
        assert 'QPushButton[danger="true"]:hover' in qss and c["error-hover"] in qss
        assert "QPushButton:hover { background: " + c["surface-hover"] in qss


def test_secondary_buttons_rely_on_their_label_not_on_the_edge():
    """Secondary buttons have a hairline (`line`) edge and a tonal fill by design: the label (>= 4.5:1) identifies them.
    Inputs, check boxes and radios keep the `border` edge (>= 3:1, see test_border_and_icon_pairs_pass_3)."""
    for theme, c in t.COLORS.items():
        assert t.contrast(c["text"], c["surface-strong"]) >= 4.5, theme
        assert t.contrast(c["line"], c["surface"]) < 3.0       # a hairline, decoration only
        assert f"QPushButton {{ border: 1px solid {c['line']}" in t.stylesheet(theme)
        assert f"QLineEdit, QPlainTextEdit {{ border: 1px solid {c['border']}" in t.stylesheet(theme)


def test_status_text_stays_readable_on_the_side_and_selected_fills():
    for theme, c in t.COLORS.items():
        for role in ("success", "warning", "error"):
            for ground in ("side", "surface-strong", "selected"):
                assert t.contrast(c[role], c[ground]) >= 4.5, (theme, role, ground)
