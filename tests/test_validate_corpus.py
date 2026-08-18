"""Unit tests for the pure-function matching logic (no AWS/TMDB calls)."""
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from utils.constants import ALT_TITLE_OVERLAP_THRESHOLD  # noqa: E402
from utils.resumable import shard_rows  # noqa: E402
from utils.text_match import best_overlap, strip_accents, title_overlap_score  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "validate_corpus", Path(__file__).resolve().parents[1] / "scripts" / "12_validate_corpus.py")
_validate_corpus = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validate_corpus)
compute_dedup_exclusions = _validate_corpus.compute_dedup_exclusions


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


# 12_validate_corpus.py auto-excludes an unresolved 06 "mismatch" verdict
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


# compute_dedup_exclusions -- merges gates 9/10/11's independent verdicts,
# gate 11 (compilation, TMDB-search-confirmed) taking precedence over 9/10
# (generic completeness proxies). test_gate11_overrides_gate10_on_the_real_case
# pins the real bug found live 2026-08-16: on the real Sheets of Gore pair,
# gate 10 alone kept the wrong id (749611, a segment) over the correct one
# (934611, the canonical compilation entry) because 749611 happens to have
# an imdb_id and 934611 doesn't -- backwards from data/excluded_compilation.csv.

def test_gate11_overrides_gate10_on_the_real_case():
    comp_rows = [
        {"segment_id": "749611", "canonical_id": "934611", "canonical_title": "Sheets of Gore",
         "resolution": "compilation_entry_found"},
        {"segment_id": "934611", "canonical_id": "934611", "canonical_title": "Sheets of Gore",
         "resolution": "compilation_entry_found"},
    ]
    # gate 10's real (wrong, in isolation) verdict: keeps the segment, drops the canonical entry
    md5_rows = [
        {"id": "749611", "keep": "1", "reason": "exact_poster_md5_dup"},
        {"id": "934611", "keep": "0", "reason": "exact_poster_md5_dup"},
    ]
    excluded = compute_dedup_exclusions(comp_rows, [], md5_rows)
    assert excluded == {"749611": "collapsed_into_compilation:Sheets of Gore"}
    assert "934611" not in excluded  # protected: it's the canonical entry, gate 10 doesn't get a say


def test_gate10_still_applies_when_gate11_has_no_opinion():
    md5_rows = [
        {"id": "1", "keep": "1", "reason": "exact_poster_md5_dup"},
        {"id": "2", "keep": "0", "reason": "exact_poster_md5_dup"},
    ]
    excluded = compute_dedup_exclusions([], [], md5_rows)
    assert excluded == {"2": "poster_md5_dup:exact_poster_md5_dup"}


def test_gate9_still_applies_when_gates_10_and_11_have_no_opinion():
    dup_rows = [
        {"id": "1", "keep": "1", "resolution": "duplicate_resolved_by_completeness_cascade"},
        {"id": "2", "keep": "0", "resolution": "duplicate_resolved_by_completeness_cascade"},
    ]
    excluded = compute_dedup_exclusions([], dup_rows, [])
    assert excluded == {"2": "tmdb_duplicate:duplicate_resolved_by_completeness_cascade"}


def test_unresolved_compilation_group_excluded_as_before():
    comp_rows = [
        {"segment_id": "1", "canonical_id": "", "canonical_title": "", "resolution": "no_compilation_entry_found"},
        {"segment_id": "2", "canonical_id": "", "canonical_title": "", "resolution": "no_compilation_entry_found"},
    ]
    excluded = compute_dedup_exclusions(comp_rows, [], [])
    assert excluded == {
        "1": "unresolved_shared_poster:no_compilation_entry_found",
        "2": "unresolved_shared_poster:no_compilation_entry_found",
    }


def test_gate11_silent_on_ids_it_never_saw():
    # a poster no gate 11 group even covers -- gates 9/10 decide normally
    dup_rows = [{"id": "5", "keep": "0", "resolution": "duplicate_resolved_by_completeness_cascade"}]
    excluded = compute_dedup_exclusions([], dup_rows, [])
    assert excluded == {"5": "tmdb_duplicate:duplicate_resolved_by_completeness_cascade"}
