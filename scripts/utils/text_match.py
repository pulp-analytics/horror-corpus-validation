"""Title/OCR text matching helpers, with accent- and space-insensitive
fallback comparisons (see docs/VALIDATION_LOGIC.md for why these three
passes exist — each one was added to fix a real false-positive found while
reviewing "mismatch" verdicts by hand)."""
from __future__ import annotations

import re
import unicodedata


def title_overlap_score(ocr_text: str, title: str) -> float:
    """Normalized token overlap of catalog title tokens found in OCR text."""

    def toks(s: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) > 1}

    tt = toks(title)
    if not tt:
        return 0.0
    ot = toks(ocr_text)
    return round(len(tt & ot) / len(tt), 4)


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def best_overlap(texts: list[str], candidates: list[str]) -> float:
    """Max title_overlap_score across every (text, candidate) pair, trying
    raw / accent-stripped / space-stripped / both variants of each side."""
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
                    best = max(best, title_overlap_score(t, c))
    return best
