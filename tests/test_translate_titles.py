"""Pure-function tests for scripts/08_translate_titles.py's
overlap_against_all_titles() -- checks a poster's OCR/translated text
against the catalog title AND every real IMDb AKA title, not just the
single primary title. No network needed.

Real 2026-08-17 finding this locks in (docs/RESULTS.md, "Validating the
Nova-mismatch/Translate reclassification without a polyglot reviewer"):
checking only the primary title made the true_mismatch bucket overcount
real matches by close to half, because a movie can have dozens of real
regional titles and a foreign poster's translated text won't always
overlap with the single ENGLISH catalog title even when it's the correct,
real title in that language."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "translate_titles", Path(__file__).resolve().parents[1] / "scripts" / "08_translate_titles.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

overlap_against_all_titles = mod.overlap_against_all_titles


def test_falls_back_to_single_title_when_no_alt_titles():
    score = overlap_against_all_titles("The Investigator", "The Investigator", [])
    assert score == 1.0


def test_matches_an_alt_title_the_primary_title_misses():
    """Real case: Dragonflies' Danish AKA is 'Øjenstikker' -- OCR text
    'Oyenstikker' scores 0.0 against the English catalog title but should
    score high against the real AKA once accent-folded."""
    score = overlap_against_all_titles("Oyenstikker", "Dragonflies", ["Øjenstikker"])
    assert score > 0.5


def test_no_match_anywhere_scores_low():
    score = overlap_against_all_titles("xyz qwerty 12345", "The Investigator", ["L'investigateur"])
    assert score < 0.3


def test_empty_alt_titles_list_same_as_no_alt_titles_column():
    with_empty = overlap_against_all_titles("The Investigator", "The Investigator", [])
    assert with_empty == 1.0
