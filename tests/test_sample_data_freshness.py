"""Guards against the exact bug found 2026-08: 06_bedrock_ocr.py and
08_translate_titles.py were both refactored (fields renamed) without
regenerating their committed data/sample_output/*.csv, so the checked-in
samples silently drifted out of sync with the scripts that produce them.
Nothing caught it -- test_bedrock_ocr.py's own docstring says main()'s
CSV plumbing is "exercised end to end by the sample data already checked
into data/sample_output/", which only holds if that sample data is
actually current.

This test reads each script's real output schema straight from its
source (no import, no execution, no AWS credentials needed) and asserts
it matches the checked-in sample CSV's header. A script's fields either
come from a literal list assigned right before the DictWriter/
open_for_append call (04, 05, 06), or from the keys of a dict literal
appended to an `out_rows` list (07, 08, 09) -- both extracted here via
`ast`, not regex, so this doesn't repeat the false-positive mistake made
diagnosing the original bug by hand (matching an unrelated cache-file
field list instead of the real --out schema).

A script not covered here either has no committed sample (01, 03 --
network/API-key dependent, nothing to check) or writes a JSON file
instead of CSV (03's alt_titles.json) -- out of scope for this check.
"""
from __future__ import annotations

import ast
import csv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SAMPLE_OUT = ROOT / "data" / "sample_output"


def _literal_list_fields(tree: ast.Module, var_name: str) -> list[str] | None:
    """Finds `<var_name> = ["a", "b", ...]` and returns the string elements."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == var_name
                and isinstance(node.value, ast.List)):
            return [el.value for el in node.value.elts if isinstance(el, ast.Constant)]
    return None


def _out_rows_dict_keys(tree: ast.Module) -> list[str] | None:
    """Finds the first `out_rows.append({"a": ..., "b": ...})` (possibly
    spanning multiple statements/kwargs) and returns the dict's keys in
    order -- covers 07/08/09's dynamic `fieldnames=list(out_rows[0].keys())`
    pattern, which the literal-list extractor above can't see."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "out_rows"
                and node.args
                and isinstance(node.args[0], ast.Dict)):
            d = node.args[0]
            keys = [k.value for k in d.keys if isinstance(k, ast.Constant)]
            if keys:
                return keys
    return None


# script -> (sample csv filename, how to extract its real fields)
CASES = {
    "06_bedrock_ocr.py": ("vision_title_check.csv", lambda t: _literal_list_fields(t, "fields")),
    "07_comprehend_language.py": ("language_detection.csv", lambda t: _literal_list_fields(t, "fields")),
    "08_translate_titles.py": ("translated_titles.csv", lambda t: _literal_list_fields(t, "fields")),
    "09_dedupe_tmdb_metadata.py": ("duplicate_resolution.csv", _out_rows_dict_keys),
    "10_dedupe_poster_md5.py": ("poster_md5_duplicates.csv", _out_rows_dict_keys),
    "11_collapse_compilations.py": ("compilation_groups.csv", _out_rows_dict_keys),
    "03_verify_poster_exists.py": ("poster_verification.csv", lambda t: _literal_list_fields(t, "fields")),
}


@pytest.mark.parametrize("script_name,sample_name,extract", [
    (s, sample, extract) for s, (sample, extract) in CASES.items()
])
def test_sample_output_schema_matches_current_script(script_name, sample_name, extract):
    script_path = SCRIPTS / script_name
    sample_path = SAMPLE_OUT / sample_name

    tree = ast.parse(script_path.read_text(encoding="utf-8"))
    real_fields = extract(tree)
    assert real_fields, (
        f"couldn't statically find {script_name}'s real output fields -- "
        f"this test's extraction logic needs updating for a code change, "
        f"not a sign the schema itself is fine"
    )

    if not sample_path.exists():
        stale = sample_path.with_suffix(sample_path.suffix + ".stale")
        if stale.exists():
            pytest.skip(f"{sample_name} is marked .stale pending a real AWS regeneration -- "
                        f"see git log for why, not a silent gap")
        pytest.fail(f"{sample_path} is missing entirely (not even a .stale marker) -- "
                    f"either regenerate it or rename it to flag it as known-stale")

    with sample_path.open(newline="", encoding="utf-8") as f:
        csv_header = next(csv.reader(f))

    assert csv_header == real_fields, (
        f"\n{sample_name}'s committed header doesn't match {script_name}'s current output fields.\n"
        f"  script produces : {real_fields}\n"
        f"  csv header is   : {csv_header}\n"
        f"This means {script_name} was refactored without regenerating this sample -- "
        f"either regenerate it for real (needs AWS credentials) or rename it to "
        f"{sample_name}.stale so downstream scripts fail loudly instead of silently "
        f"reading the wrong columns."
    )
