"""Shared resumability helper: if --out already has rows for some ids
(e.g. a previous run got interrupted), skip those and append the rest
instead of starting over and re-spending API calls on work already done."""
from __future__ import annotations

import csv
from pathlib import Path


def load_done_ids(path: Path, id_col: str = "id") -> set[str]:
    """ids already present in an existing output CSV, or empty set if none."""
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        try:
            return {row[id_col] for row in csv.DictReader(f) if row.get(id_col)}
        except (KeyError, csv.Error):
            return set()


def open_for_append(path: Path, fieldnames: list[str]) -> tuple:
    """Returns (file_handle, DictWriter). Writes the header only if the
    file didn't already exist -- so re-running after an interruption
    appends cleanly instead of duplicating a header mid-file."""
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a" if not is_new else "w", newline="", encoding="utf-8")
    w = csv.DictWriter(f, fieldnames=fieldnames)
    if is_new:
        w.writeheader()
    return f, w


def shard_rows(rows: list[dict], shard_index: int, shard_count: int) -> list[dict]:
    """Deterministic partition of --in's rows by position, for running N
    copies of a script in parallel over disjoint slices of the same file
    (e.g. one per AWS Batch array job index -- see the sibling
    poster-analysis-infrastructure repo). shard_count=1 (the default)
    returns every row unchanged, so this is a no-op unless you opt in."""
    if shard_count <= 1:
        return rows
    if not (0 <= shard_index < shard_count):
        raise ValueError(f"shard_index {shard_index} out of range for shard_count {shard_count}")
    return rows[shard_index::shard_count]


def write_csv_rows(out_path: Path | str, rows: list[dict]) -> None:
    """Writes a full --out CSV from an in-memory list of dicts (as opposed
    to open_for_append's incremental per-row writes), inferring fieldnames
    from the first row. No-op if rows is empty -- callers that always want
    a file on disk should check for that themselves."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
