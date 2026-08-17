"""Title/OCR text matching helpers, with accent- and space-insensitive
fallback comparisons (see docs/VALIDATION_LOGIC.md for why these three
passes exist — each one was added to fix a real false-positive found while
reviewing "mismatch" verdicts by hand)."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def title_overlap_score(ocr_text: str, title: str) -> float:
    """Normalized token overlap of catalog title tokens found in OCR text."""

    def toks(s: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) > 1}

    tt = toks(title)
    if not tt:
        return 0.0
    ot = toks(ocr_text)
    return round(len(tt & ot) / len(tt), 4)


def normalize_alnum(s: str) -> str:
    """Strip non-alphanumeric (handles glued tokens / spacing differences
    between an OCR read and the catalog title)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def title_fuzzy_score(ocr_text: str, title: str) -> float:
    """SequenceMatcher ratio on alnum-normalized title vs. full OCR string
    -- catches close-but-not-token-exact reads that title_overlap_score's
    token-set comparison misses (e.g. OCR'd extra/missing spaces).
    Ported as-is from the real project's ocr_metrics.py."""
    a = normalize_alnum(title)
    b = normalize_alnum(ocr_text)
    if not a or not b:
        return 0.0
    return round(SequenceMatcher(None, a, b).ratio(), 4)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def best_overlap(texts: list[str], candidates: list[str]) -> float:
    """Max of title_overlap_score (token sets) AND title_fuzzy_score
    (character-sequence ratio) across every (text, candidate) pair, trying
    raw / accent-stripped / space-stripped / both variants of each side.
    Both scorers matter, not just one: token overlap catches word-level
    matches; fuzzy ratio catches single-word titles that differ by only a
    couple of characters (real 2026-08-17 case: OCR text "Oyenstikker"
    vs. the real Danish AKA "Øjenstikker" -- 0.0 token overlap since
    neither shares a token with the other verbatim, but 0.86 fuzzy ratio,
    because normalize_alnum's ASCII-only strip incidentally drops "Ø" and
    the two 10-11 char strings are otherwise nearly identical). See
    docs/RESULTS.md, "Validating the Nova-mismatch/Translate
    reclassification without a polyglot reviewer"."""
    best = 0.0
    for text in texts:
        if not text:
            continue
        variants_t = [text, strip_accents(text), text.replace(" ", ""), strip_accents(text).replace(" ", "")]
        for cand in candidates:
            if not cand:
                continue
            variants_c = [cand, strip_accents(cand), cand.replace(" ", ""), strip_accents(cand).replace(" ", "")]
            for t in variants_t:
                for c in variants_c:
                    best = max(best, title_overlap_score(t, c), title_fuzzy_score(t, c))
    return best
