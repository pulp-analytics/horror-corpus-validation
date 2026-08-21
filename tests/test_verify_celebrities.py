"""Pure-function tests for scripts/16_verify_celebrities.py -- name_match()
(ported as-is from the real project's verify_celebrities_vs_cast.py) and
the clearly_wrong-only flagging rule described in the gate's own docstring.
No AWS calls needed."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "verify_celebrities", Path(__file__).resolve().parents[1] / "scripts" / "16_verify_celebrities.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

name_match = mod.name_match


def test_exact_match():
    assert name_match("Robert De Niro", "Robert De Niro") is True


def test_substring_match():
    # one name contained in the other -- handles a truncated/partial credit
    assert name_match("Robert De Niro", "Robert De Niro Jr") is True


def test_formatting_difference_still_matches():
    # accent/spacing-insensitive after _norm() strips to [a-z] only
    assert name_match("Robert DeNiro", "Robert De Niro") is True


def test_fuzzy_close_match():
    # a single-character typo should still clear the 0.85 ratio threshold
    assert name_match("Danilo Pereira", "Danilo Pereyra") is True


def test_different_people_do_not_match():
    assert name_match("Brad Pitt", "Brad Renfro") is False


def test_empty_names_never_match():
    assert name_match("", "Brad Pitt") is False
    assert name_match("Brad Pitt", "") is False
    assert name_match("", "") is False


def test_case_insensitive():
    assert name_match("brad pitt", "Brad Pitt") is True


def _flag_from_verdicts(verdicts: list[dict]) -> tuple[bool, list[str]]:
    """Mirrors the inline flagging logic in process_one(): only
    clearly_wrong verdicts produce a flag reason."""
    reasons = [f"celebrity_not_in_cast:{v['name']}" for v in verdicts if v["verdict"] == "clearly_wrong"]
    return bool(reasons), reasons


def test_plausible_verdict_does_not_flag():
    flagged, reasons = _flag_from_verdicts([{"name": "Eric Roberts", "verdict": "plausible", "reason": "uncredited extra"}])
    assert flagged is False
    assert reasons == []


def test_uncertain_verdict_does_not_flag():
    flagged, reasons = _flag_from_verdicts([{"name": "Jane Doe", "verdict": "uncertain", "reason": "not enough info"}])
    assert flagged is False
    assert reasons == []


def test_clearly_wrong_verdict_flags():
    flagged, reasons = _flag_from_verdicts([{"name": "Amedeo Avogadro", "verdict": "clearly_wrong", "reason": "died 1856, before photography"}])
    assert flagged is True
    assert reasons == ["celebrity_not_in_cast:Amedeo Avogadro"]


def test_mixed_verdicts_flag_only_on_the_clearly_wrong_name():
    flagged, reasons = _flag_from_verdicts([
        {"name": "Eric Roberts", "verdict": "plausible", "reason": "uncredited extra"},
        {"name": "Tipu Sultan", "verdict": "clearly_wrong", "reason": "died 1799"},
    ])
    assert flagged is True
    assert reasons == ["celebrity_not_in_cast:Tipu Sultan"]


def test_no_unmatched_names_no_flag():
    flagged, reasons = _flag_from_verdicts([])
    assert flagged is False
    assert reasons == []
