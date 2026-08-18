"""Pure-function tests for scripts/14_score_alternate_posters.py's
propose_swap() -- the real project's 3-rule swap decision, ported as-is
from score_multi_poster_variants_ocr.py. No AWS/network needed."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "score_alternate_posters", Path(__file__).resolve().parents[1] / "scripts" / "14_score_alternate_posters.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

propose_swap = mod.propose_swap
score_text = mod.score_text
propose_swap_poster_type = mod.propose_swap_poster_type


def variant(overlap_max, fuzzy_max, ocr_chars=20, file_path="/v.jpg"):
    return {"overlap_max": overlap_max, "fuzzy_max": fuzzy_max, "ocr_chars": ocr_chars, "file_path": file_path}


def test_overlap_gain_rule_fires():
    current = {"overlap_max": 0.1, "fuzzy_max": 0.1}
    variants = [variant(overlap_max=0.6, fuzzy_max=0.6)]
    d = propose_swap(current, variants, min_best=0.40, min_gain=0.25, min_fuzzy_best=0.55)
    assert d["propose"] == 1
    assert d["reason"] == "overlap_gain"


def test_no_swap_when_gain_too_small():
    current = {"overlap_max": 0.5, "fuzzy_max": 0.5}
    variants = [variant(overlap_max=0.6, fuzzy_max=0.6)]  # gain=0.1, below min_gain
    d = propose_swap(current, variants, min_best=0.40, min_gain=0.25, min_fuzzy_best=0.55)
    assert d["propose"] == 0
    assert d["reason"] == "no"


def test_fuzzy_gain_rule_requires_overlap_floor():
    # high fuzzy gain but overlap_max below the 0.2 floor the real rule requires
    current = {"overlap_max": 0.0, "fuzzy_max": 0.0}
    variants = [variant(overlap_max=0.1, fuzzy_max=0.9)]
    d = propose_swap(current, variants, min_best=0.40, min_gain=0.25, min_fuzzy_best=0.55)
    assert d["propose"] == 0  # overlap 0.1 < the fuzzy rule's 0.2 floor


def test_fuzzy_gain_rule_fires_with_overlap_floor_met():
    current = {"overlap_max": 0.0, "fuzzy_max": 0.0}
    variants = [variant(overlap_max=0.25, fuzzy_max=0.9)]
    d = propose_swap(current, variants, min_best=0.40, min_gain=0.25, min_fuzzy_best=0.55)
    assert d["propose"] == 1
    assert d["reason"] == "fuzzy_gain"


def test_current_near_zero_rule():
    # current overlap is near-zero and best variant clears min_best, even
    # with a small absolute gain
    current = {"overlap_max": 0.05, "fuzzy_max": 0.05}
    variants = [variant(overlap_max=0.41, fuzzy_max=0.2)]
    d = propose_swap(current, variants, min_best=0.40, min_gain=0.9, min_fuzzy_best=0.99)
    assert d["propose"] == 1
    assert d["reason"] == "current_near_zero"


def test_no_current_poster_treated_as_zero():
    # current=None (e.g. primary poster fetch failed) -- should behave
    # like current_overlap=0.0, not crash
    variants = [variant(overlap_max=0.5, fuzzy_max=0.5)]
    d = propose_swap(None, variants, min_best=0.40, min_gain=0.25, min_fuzzy_best=0.55)
    assert d["propose"] == 1
    assert d["current_overlap"] == 0.0


def test_best_variant_picked_by_overlap_then_fuzzy_then_chars():
    current = {"overlap_max": 0.0, "fuzzy_max": 0.0}
    variants = [
        variant(overlap_max=0.3, fuzzy_max=0.9, file_path="/lower_overlap.jpg"),
        variant(overlap_max=0.6, fuzzy_max=0.1, file_path="/higher_overlap.jpg"),
    ]
    d = propose_swap(current, variants, min_best=0.40, min_gain=0.25, min_fuzzy_best=0.55)
    assert d["best"]["file_path"] == "/higher_overlap.jpg"


def test_score_text_takes_max_across_title_and_original_title():
    # text matches original_title well but not the (English) catalog title --
    # overlap_max/fuzzy_max should reflect the better of the two
    result = score_text("La Casa Del Terror", "The House of Terror", "La Casa Del Terror")
    assert result["overlap_original"] == 1.0
    assert result["overlap_max"] == 1.0


def test_score_text_empty_text_is_zero_not_error():
    result = score_text("", "Some Title", "Some Title")
    assert result["overlap_max"] == 0.0
    assert result["fuzzy_max"] == 0.0
    assert result["ocr_chars"] == 0


# -- --mode poster-type: propose_swap_poster_type() (gate 4's rescue) --

def poster_type_variant(is_poster, file_path="/v.jpg"):
    return variant(overlap_max=1.0 if is_poster else 0.0, fuzzy_max=0.0, ocr_chars=0, file_path=file_path)


def test_poster_type_rescue_fires_when_a_real_poster_variant_exists():
    variants = [poster_type_variant(False, "/not_a_poster.jpg"), poster_type_variant(True, "/real_poster.jpg")]
    d = propose_swap_poster_type(variants)
    assert d["propose"] == 1
    assert d["reason"] == "poster_type_rescue"
    assert d["best"]["file_path"] == "/real_poster.jpg"


def test_poster_type_rescue_picks_first_real_poster_not_highest_score():
    # both variants score the same (is_movie_poster=True) -- rescue picks
    # the first one in TMDB's own rank order, not a "best of N" comparison
    # the way title-match mode's overlap/fuzzy gain does.
    variants = [poster_type_variant(True, "/first.jpg"), poster_type_variant(True, "/second.jpg")]
    d = propose_swap_poster_type(variants)
    assert d["best"]["file_path"] == "/first.jpg"


def test_poster_type_no_rescue_when_no_variant_is_a_real_poster():
    variants = [poster_type_variant(False, "/still_not_a_poster.jpg")]
    d = propose_swap_poster_type(variants)
    assert d["propose"] == 0
    assert d["reason"] == "no_real_poster_alternative"


def test_poster_type_no_current_concept_zeroed():
    # gate 4 already confirmed the current poster is_movie_poster=False --
    # there's no "current" baseline to compute a gain against.
    variants = [poster_type_variant(True)]
    d = propose_swap_poster_type(variants)
    assert d["current_overlap"] == 0.0
    assert d["current_fuzzy"] == 0.0
    assert d["gain_overlap"] == 0.0
    assert d["gain_fuzzy"] == 0.0
