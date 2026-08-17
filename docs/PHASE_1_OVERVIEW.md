# Phase 1: Corpus Validation — Overview

This repo covers one phase of a larger horror-movie-poster analysis project
(145k+ titles across horror + adjacent genres, sourced from TMDB): making
sure the corpus is actually correct before running any downstream metrics
on it.

Three separate but related problems, discovered while spot-checking results
by hand:

1. **Duplicate TMDB entries** — the same film listed twice under different
   TMDB ids, usually with different (one correct, one wrong/cropped) posters.
2. **Compilation/anthology posters** — a poster shared across several
   catalog entries because it's really the cover of a compilation tape, DVD
   boxset, or TV anthology, not individual poster art per film/segment.
3. **Poster/title mismatches** — the image doesn't show the catalog title at
   all, which can mean a genuinely wrong poster, or a legitimate foreign/
   reissue title our metadata didn't have on file.

## Why this matters

None of the above show up if you only look at aggregate stats — a wrong
poster still has *a* color palette, *a* CLIP embedding, *a* face detection
result. The corpus looks complete and the pipeline runs fine; the numbers
are just measuring the wrong image for those rows. Catching this requires
cross-referencing multiple independent signals (OCR, a vision-LLM's own
read of the image, alternate-title databases) rather than trusting any one
of them alone — see [VALIDATION_LOGIC.md](VALIDATION_LOGIC.md) for the
decision tree and [RESULTS.md](RESULTS.md) for what it actually found on
our corpus.

## Pipeline order

```
01_tmdb_enumerate       → raw candidate ids
03_verify_poster_exists → does this row even have a reachable poster?
05_fetch_alt_titles     → alternate titles (TMDB + optional IMDb) -- used to review 04's mismatches, not a match itself
06_bedrock_ocr          → what a vision-LLM actually reads on the poster
07_comprehend_language  → language of that text
08_translate_titles     → re-score non-English text against the catalog title
09_dedupe_tmdb_metadata → same title+year+overview, different id?
10_dedupe_poster_md5    → same exact poster image file used for two ids?
11_collapse_compilations → same poster shared across several ids?
12_validate_corpus      → orchestrates 1-9, writes the final validated CSV
```

Quick start: see the root [README.md](../README.md).
