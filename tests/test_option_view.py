"""option_view: the Qt-free rule for which options apply. Exhaustive over every tick combination."""
import itertools

from shiva import ui_model as m

NAMES = m.OPTION_NAMES


def _states():
    for tab in m.TABS:
        for mode in ("encrypt", "decrypt"):
            for can in (False, True):
                for bits in itertools.product((False, True), repeat=len(NAMES)):
                    for reenc in (False, True):
                        yield tab, mode, dict(zip(NAMES, bits)), m.OptionFacts(can, reenc)


def _previous(tab, mode, ticks, can):
    """The earlier widget logic, written over the same inputs: a hidden option was un-ticked and ignored."""
    enc = tab == "clean" and ticks["encrypt_cleaned"]
    shred_shown = (tab == "clean" and ticks["encrypt_cleaned"]) or (tab == "encrypt" and mode == "encrypt")
    shred = ticks["shred_after"] and shred_shown
    st = {tab} | ({"encrypt"} if enc else set()) | ({"shred"} if shred else set())
    more = "clean" in st and ticks["more"]
    lossless = more and ticks["keep_quality"]
    allow = tab == "clean" and "encrypt" in st and can and ticks["allow_unclean"]
    return {
        "shred": shred, "allow_unclean": allow,
        "out": bool(st & {"clean", "encrypt"}) and ticks["out"],
        "unknown": "keep" if more and ticks["keep_parts"] else "omit" if more and ticks["omit_parts"] else "abort",
        "jpeg_mode": "lossless" if lossless else "reencode",
        "orient": lossless and ticks["keep_orient"],
        "chroma": more and not lossless and ticks["chroma420"],
        "icc": more and ticks["keep_icc"],
    }


def test_not_shown_or_not_enabled_is_never_live_and_decisions_match():
    n = 0
    for tab, mode, ticks, facts in _states():
        v = m.option_view(tab, mode, ticks, facts)
        for name in NAMES:
            assert v.enabled[name] <= v.shown[name]
            assert v.live[name] == (v.shown[name] and v.enabled[name] and ticks[name])
            if not (v.shown[name] and v.enabled[name]):
                assert not v.live[name]
        assert v.live["encrypt_cleaned"] == (tab == "clean" and ticks["encrypt_cleaned"])
        for note, owner in (("keep_quality_hint", "keep_quality"), ("keep_quality_more", "keep_quality"),
                            ("keep_parts_hint", "keep_parts"), ("unclean_hint", "allow_unclean")):
            assert v.shown[note] == v.live[owner]
        # the re-encode warning: shown iff cleaning runs, a listed file may hold such an image, and the data is not kept as is
        assert v.shown["reencode_hint"] == (tab == "clean" and facts.reencodes and not v.live["keep_quality"])
        assert v.enabled["reencode_hint"] == v.shown["reencode_hint"]
        old = _previous(tab, mode, ticks, facts.can_unclean)
        live = v.live
        assert live["shred_after"] == old["shred"]
        assert live["allow_unclean"] == old["allow_unclean"]
        assert live["out"] == old["out"]
        assert ("keep" if live["keep_parts"] else "omit" if live["omit_parts"] else "abort") == old["unknown"]
        assert ("lossless" if live["keep_quality"] else "reencode") == old["jpeg_mode"]
        assert live["keep_orient"] == old["orient"] and live["chroma420"] == old["chroma"]
        assert live["keep_icc"] == old["icc"]
        n += 1
    assert n == 3 * 2 * 2 * 2 * 2 ** len(NAMES)


def test_shred_page_has_no_options():
    v = m.option_view("shred", "encrypt", {n: True for n in NAMES}, m.OptionFacts(True))
    assert not any(v.live.values()) and not any(v.shown[n] for n in NAMES)


def test_disabled_child_stays_shown_with_a_reason():
    v = m.option_view("clean", "encrypt", {}, m.OptionFacts())
    assert v.shown["shred_after"] and not v.enabled["shred_after"] and v.shown["shred_after_reason"]
    v = m.option_view("clean", "encrypt", {"encrypt_cleaned": True}, m.OptionFacts())
    assert v.enabled["shred_after"] and not v.shown["shred_after_reason"]
    v = m.option_view("encrypt", "decrypt", {"shred_after": True}, m.OptionFacts())
    assert v.shown["shred_after"] and not v.live["shred_after"] and v.shown["shred_after_reason"]
