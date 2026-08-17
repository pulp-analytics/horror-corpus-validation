"""Pure-function tests for scripts/15_content_moderation.py -- the real
thresholds (NOVA_THRESHOLD=0.5, REK_THRESHOLD=0.4) ported from
nova_enrich_live_summary.py and rekognition_enrich.py's own real usage,
plus the _score/_mod_score helpers ported as-is from the real project's
nova_poster_enrich.py / rekognition_enrich.py. No AWS calls needed."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "content_moderation", Path(__file__).resolve().parents[1] / "scripts" / "15_content_moderation.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

compute_flag = mod.compute_flag
_score = mod._score
_mod_score = mod._mod_score


def row(**kwargs):
    base = {f: "" for f in mod.OUT_FIELDS}
    base.update(kwargs)
    return base


def test_unflagged_when_everything_below_threshold():
    r = row(nova_blood_gore=0.3, nova_violence=0.2, nova_sexual_content=0.0,
            rek_gore=0.1, rek_violence=0.0, rek_nudity=0.0, rek_suggestive=0.0)
    flagged, reasons = compute_flag(r)
    assert flagged is False
    assert reasons == []


def test_nova_blood_gore_at_real_threshold_flags():
    r = row(nova_blood_gore=0.5, nova_violence=0.0, nova_sexual_content=0.0,
            rek_gore=0.0, rek_violence=0.0, rek_nudity=0.0, rek_suggestive=0.0)
    flagged, reasons = compute_flag(r)
    assert flagged is True
    assert reasons == ["nova_blood_gore>=0.5"]


def test_rekognition_gore_at_real_threshold_flags():
    r = row(nova_blood_gore=0.0, nova_violence=0.0, nova_sexual_content=0.0,
            rek_gore=0.4, rek_violence=0.0, rek_nudity=0.0, rek_suggestive=0.0)
    flagged, reasons = compute_flag(r)
    assert flagged is True
    assert reasons == ["rek_gore>=0.4"]


def test_either_engine_alone_is_enough_no_agreement_required():
    # nova says clean, rekognition flags nudity -- still flagged overall
    r = row(nova_blood_gore=0.0, nova_violence=0.0, nova_sexual_content=0.1,
            rek_gore=0.0, rek_violence=0.0, rek_nudity=0.6, rek_suggestive=0.0)
    flagged, reasons = compute_flag(r)
    assert flagged is True
    assert reasons == ["rek_nudity>=0.4"]


def test_multiple_reasons_all_reported():
    r = row(nova_blood_gore=0.9, nova_violence=0.8, nova_sexual_content=0.0,
            rek_gore=0.9, rek_violence=0.0, rek_nudity=0.0, rek_suggestive=0.0)
    flagged, reasons = compute_flag(r)
    assert flagged is True
    assert set(reasons) == {"nova_blood_gore>=0.5", "nova_violence>=0.5", "rek_gore>=0.4"}


def test_blank_string_fields_treated_as_unset_not_error():
    # a row that errored out (image fetch failed) has "" for every score field
    r = row()
    flagged, reasons = compute_flag(r)
    assert flagged is False
    assert reasons == []


def test_score_clamps_to_0_1_range():
    assert _score(1.5) == 1.0
    assert _score(-0.3) == 0.0
    assert _score(0.7) == 0.7


def test_score_defaults_on_bad_input():
    assert _score(None) == 0.0
    assert _score("not-a-number") == 0.0


def test_mod_score_picks_best_matching_label_case_insensitive():
    mods = [("Blood & Gore", 0.91), ("Violence", 0.3), ("Weapons", 0.6)]
    assert _mod_score(mods, "Blood & Gore", "Gore") == 0.91
    assert _mod_score(mods, "violence", "Graphic Violence") == 0.3


def test_mod_score_zero_when_no_label_matches():
    mods = [("Rain", 0.5)]
    assert _mod_score(mods, "Violence", "Gore") == 0.0
