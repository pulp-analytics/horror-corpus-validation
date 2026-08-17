"""Guards against the exact class of bug found live 2026-08-16:
sample_100_ids.csv's second Omegle row had an empty `overview` cell,
silently breaking 09_dedupe_tmdb_metadata.py's exact-match grouping key.
The pair never even became a "candidate group" -- 07 logged "0 candidate
groups" and moved on, no error, nothing to catch except running it by
hand against a real TMDB key and noticing the count looked wrong.

test_sample_data_freshness.py already guards schema (column names). This
guards *values* -- that the specific real examples this repo documents
and cites (docs/RESULTS.md, docs/VALIDATION_LOGIC.md) still satisfy the
structural precondition each gate needs to even consider them, checked
directly against the committed fixtures, no network calls."""
import csv
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("dedupe_tmdb_metadata", SCRIPTS / "09_dedupe_tmdb_metadata.py")
_dedupe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dedupe)
norm = _dedupe.norm


def _group_key(row: dict) -> tuple:
    return (norm(row.get("title", "")), (row.get("release_date", "") or "")[:4], norm(row.get("overview", "")))


def test_real_omegle_pair_still_groups_under_gate7():
    """The real duplicate example cited in docs/RESULTS.md and
    data/sample_output/duplicate_resolution.csv. If either row's title,
    release_date, or overview cell ever goes blank or drifts out of sync
    with the other, this fails immediately -- instead of 07 silently
    reporting "0 candidate groups" the next time someone runs it."""
    with (ROOT / "data" / "sample_input" / "sample_100_ids.csv").open(newline="", encoding="utf-8") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}

    omegle_ids = ["1009049", "1743173"]
    for id_ in omegle_ids:
        assert id_ in rows, f"real Omegle id {id_} missing from sample_100_ids.csv"

    keys = {id_: _group_key(rows[id_]) for id_ in omegle_ids}
    first_key = keys[omegle_ids[0]]
    assert all(first_key), f"grouping key has an empty field: {first_key}"
    assert first_key == keys[omegle_ids[1]], (
        f"the real Omegle pair no longer shares a grouping key -- "
        f"{omegle_ids[0]}={first_key!r} vs {omegle_ids[1]}={keys[omegle_ids[1]]!r}"
    )


def test_real_sheets_of_gore_pair_shares_a_poster_path():
    """The real compilation example (data/excluded_compilation.csv,
    docs/VALIDATION_LOGIC.md) -- 11_collapse_compilations.py only ever
    considers a group of 2+ ids sharing the exact same poster_path, so if
    this fixture's two poster_path cells ever drift apart, the group
    stops existing and the whole test case silently goes untested."""
    fixture = ROOT / "tests" / "sample_data" / "compilation_shared_poster.csv"
    with fixture.open(newline="", encoding="utf-8") as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}

    segment_id, canonical_id = "749611", "934611"
    assert segment_id in rows and canonical_id in rows
    assert rows[segment_id]["poster_path"], "poster_path is empty -- not a real shared-poster fixture anymore"
    assert rows[segment_id]["poster_path"] == rows[canonical_id]["poster_path"]
