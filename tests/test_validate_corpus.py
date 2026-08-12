"""Unit tests for the pure-function matching logic (no AWS/TMDB calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from utils.text_match import best_overlap, strip_accents, title_overlap_score  # noqa: E402


def test_exact_match():
    assert title_overlap_score("The Ring", "The Ring") == 1.0


def test_no_overlap():
    assert title_overlap_score("Completely Unrelated Text", "The Ring") == 0.0


def test_partial_overlap():
    score = title_overlap_score("A RING appears somewhere", "The Ring")
    assert 0 < score < 1.0


def test_case_insensitive():
    assert title_overlap_score("the ring", "THE RING") == 1.0


def test_strip_accents():
    assert strip_accents("Isoäiti helvetistä") == "Isoaiti helvetista"


def test_best_overlap_needs_accent_normalization():
    # raw comparison would score 0 -- only the accent-stripped variant matches
    score = best_overlap(["ISOAITI HELVETISTA"], ["Isoäiti helvetistä"])
    assert score == 1.0


def test_best_overlap_needs_space_normalization():
    score = best_overlap(["the poster says BlackOps"], ["Black Ops"])
    assert score == 1.0


def test_best_overlap_empty_inputs():
    assert best_overlap([], ["Black Ops"]) == 0.0
    assert best_overlap(["some text"], []) == 0.0
    assert best_overlap([""], [""]) == 0.0
