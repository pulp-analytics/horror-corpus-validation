"""Pure-function tests for scripts/11_collapse_compilations.py's
best_compilation_match() -- picks the best-scoring TMDB search result for
a shared-poster compilation. No network needed.

test_real_id_can_be_its_own_match locks in a real, live-checked case
(2026-08-16): TMDB's actual entry for "Sheets of Gore" (id 934611) is
itself one of the rows sharing the old poster_path in the real data --
an earlier version of this function excluded candidates matching the
input group's own ids and would have wrongly rejected the correct
answer. See the script's module docstring for the full story."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "collapse_compilations", Path(__file__).resolve().parents[1] / "scripts" / "11_collapse_compilations.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

best_compilation_match = mod.best_compilation_match
has_distinct_segment_titles = mod.has_distinct_segment_titles


def test_picks_best_scoring_candidate_among_several():
    candidates = [
        {"id": 1, "title": "Some Unrelated Movie"},
        {"id": 2, "title": "Sheets of Gore"},
        {"id": 3, "title": "Sheet Metal"},
    ]
    match = best_compilation_match("sheets of gore", candidates)
    assert match["canonical_id"] == "2"
    assert match["canonical_title"] == "Sheets of Gore"


def test_real_id_can_be_its_own_match():
    candidates = [{"id": 934611, "title": "Sheets of Gore"}]
    match = best_compilation_match("sheets of gore", candidates)
    assert match["canonical_id"] == "934611"


def test_below_threshold_returns_no_match():
    candidates = [{"id": 1, "title": "Completely Different Title"}]
    match = best_compilation_match("sheets of gore", candidates, min_score=0.55)
    assert match["canonical_id"] == ""
    assert match["score"] < 0.55


def test_empty_candidates_returns_no_match():
    match = best_compilation_match("sheets of gore", [])
    assert match == {"canonical_id": "", "canonical_title": "", "score": 0.0}


def test_does_not_require_exactly_one_search_result():
    candidates = [
        {"id": 1, "title": "Sheets of Gore"},
        {"id": 2, "title": "Some Other Movie Entirely"},
        {"id": 3, "title": "Yet Another Unrelated Title"},
    ]
    match = best_compilation_match("sheets of gore", candidates)
    assert match["canonical_id"] == "1"


def test_distinct_real_series_titles_confirmed_not_compilation():
    """Real 2026-08-17 case: The Hazards of Helen -- 106 individually-
    titled 1915-1916 silent-serial episodes sharing one generic poster.
    Distinct segment titles are real evidence this isn't a mis-split
    compilation, just a series reusing stock art."""
    titles = [
        "The Hazards of Helen: Episode13, The Escape on the Fast Freight",
        "The Hazards of Helen Ep26: The Wild Engine",
        "The Leap from the Water Tower",
        "The Death Train",
        "The Capture of Red Stanley",
    ]
    assert has_distinct_segment_titles(titles) is True


def test_near_duplicate_titles_not_confirmed():
    """Real 2026-08-17 case: 'Kamen Rider BiBiBi no Bibill Geiz' vs
    '...BibillGeiz' -- a spacing-only duplicate of the same title, not a
    compilation case at all. Should NOT be confirmed as a real series."""
    titles = ["Kamen Rider BiBiBi no Bibill Geiz", "Kamen Rider BiBiBi no BibillGeiz"]
    assert has_distinct_segment_titles(titles) is False


def test_single_segment_is_not_distinct():
    assert has_distinct_segment_titles(["Only One Title"]) is False


def test_empty_titles_are_not_distinct():
    assert has_distinct_segment_titles(["", "", ""]) is False


def test_mostly_identical_titles_not_confirmed():
    titles = ["Same Movie", "Same Movie", "Same Movie", "Different Movie"]
    assert has_distinct_segment_titles(titles) is False
