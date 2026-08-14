"""Pure-function tests for utils/resumable.py's write_csv_rows -- the new
shared helper factored out of 07/08/09's identical write-out block."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from utils.resumable import write_csv_rows


def test_write_csv_rows_writes_header_and_rows(tmp_path):
    out = tmp_path / "nested" / "out.csv"
    write_csv_rows(out, [{"id": "1", "keep": "1"}, {"id": "2", "keep": "0"}])

    with out.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == [{"id": "1", "keep": "1"}, {"id": "2", "keep": "0"}]


def test_write_csv_rows_empty_list_is_noop(tmp_path):
    out = tmp_path / "out.csv"
    write_csv_rows(out, [])
    assert not out.exists()


def test_write_csv_rows_fieldnames_from_first_row(tmp_path):
    out = tmp_path / "out.csv"
    write_csv_rows(out, [{"a": "1", "b": "2"}])

    with out.open(newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == ["a", "b"]
