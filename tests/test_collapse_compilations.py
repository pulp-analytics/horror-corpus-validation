"""Pure-function tests for scripts/09_collapse_compilations.py's
best_compilation_match() -- picks the best-scoring TMDB search result for
a shared-poster compilation, excluding the segments' own ids. No network
needed."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "collapse_compilations", Path(__file__).resolve().parents[1] / "scripts" / "09_collapse_compilations.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

best_compilation_match = mod.best_compilation_match


def test_picks_best_scoring_candidate_among_several():
    candidates = [
        {"id": 1, "title": "Some Unrelated Movie"},
        {"id": 2, "title": "Sheets of Gore"},
        {"id": 3, "title": "Sheet Metal"},
    ]
    match = best_compilation_match("sheets of gore", candidates, exclude_ids=set())
    assert match["canonical_id"] == "2"
    assert match["canonical_title"] == "Sheets of Gore"


def test_excludes_segment_ids_even_if_best_scoring():
    candidates = [{"id": 42, "title": "Sheets of Gore"}]
    match = best_compilation_match("sheets of gore", candidates, exclude_ids={"42"})
    assert match["canonical_id"] == ""


def test_below_threshold_returns_no_match():
    candidates = [{"id": 1, "title": "Completely Different Title"}]
    match = best_compilation_match("sheets of gore", candidates, exclude_ids=set(), min_score=0.55)
    assert match["canonical_id"] == ""
    assert match["score"] < 0.55


def test_empty_candidates_returns_no_match():
    match = best_compilation_match("sheets of gore", [], exclude_ids=set())
    assert match == {"canonical_id": "", "canonical_title": "", "score": 0.0}


def test_does_not_require_exactly_one_search_result():
    candidates = [
        {"id": 1, "title": "Sheets of Gore"},
        {"id": 2, "title": "Some Other Movie Entirely"},
        {"id": 3, "title": "Yet Another Unrelated Title"},
    ]
    match = best_compilation_match("sheets of gore", candidates, exclude_ids=set())
    assert match["canonical_id"] == "1"
