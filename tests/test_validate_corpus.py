"""Unit tests for the pure-function matching logic (no AWS/TMDB calls)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from utils.constants import ALT_TITLE_OVERLAP_THRESHOLD  # noqa: E402
from utils.resumable import shard_rows  # noqa: E402
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


# 10_validate_corpus.py auto-excludes an unresolved 04 "mismatch" verdict
# when best_overlap(poster texts, [catalog title, *alt titles]) doesn't clear
# ALT_TITLE_OVERLAP_THRESHOLD. These pin the real cases that decision covers.

def test_mismatch_resolved_by_alt_title():
    # real case: poster read "World of the Living Dead", a genuine AKA
    score = best_overlap(["World of the Living Dead"], ["Zombie Creeping Flesh", "World of the Living Dead"])
    assert score > ALT_TITLE_OVERLAP_THRESHOLD


def test_mismatch_resolved_by_translation():
    # real case: poster text needed accent-stripping after translation to line up
    score = best_overlap(["Isoaiti helvetista"], ["Isoäiti helvetistä"])
    assert score > ALT_TITLE_OVERLAP_THRESHOLD


def test_mismatch_stays_unresolved_without_matching_evidence():
    score = best_overlap(["Totally Unrelated Poster Text"], ["Catalog Movie Title"])
    assert score <= ALT_TITLE_OVERLAP_THRESHOLD


# shard_rows -- used by 02/03/04/05/06 for AWS Batch array-job sharding
# (poster-analysis-infrastructure). Every shard together must reconstruct
# the original rows exactly once each, with no overlap and no gaps.

def test_shard_rows_no_sharding_by_default():
    rows = [{"id": str(i)} for i in range(10)]
    assert shard_rows(rows, 0, 1) == rows


def test_shard_rows_partition_is_exhaustive_and_disjoint():
    rows = [{"id": str(i)} for i in range(23)]  # not evenly divisible by 4
    shard_count = 4
    shards = [shard_rows(rows, i, shard_count) for i in range(shard_count)]
    seen_ids = [r["id"] for shard in shards for r in shard]
    assert sorted(seen_ids, key=int) == [r["id"] for r in rows]
    assert len(seen_ids) == len(rows)  # no duplicates across shards


def test_shard_rows_rejects_out_of_range_index():
    with pytest.raises(ValueError):
        shard_rows([{"id": "1"}], shard_index=4, shard_count=4)
