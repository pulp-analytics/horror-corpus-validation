# Results — full corpus run

These are the real numbers from running this validation methodology against
our full horror-genre corpus (a subset of a larger 145k-title multi-genre
project). This document is the ground truth the sample scripts in this repo
are simplified, portable versions of.

## Headline numbers

| | Count |
|---|---|
| Horror corpus, before validation | 69,789 |
| Horror corpus, after validation | 69,437 |
| Removed | 352 (0.5%) |

## Breakdown by category

| Category | Removed (horror-scoped) |
|---|---|
| TMDB duplicate entries, resolved | 8 |
| Exact poster image duplicates (MD5) | 3 |
| Compilation/anthology posters, collapsed | 11 |
| Dead TMDB ids (404, confirmed live) | 6 |
| No verifiable poster (empty `poster_path`, unreproducible analysis) | 327 |
| **Total** | **352*** |

\* MD5 dedup ran earlier in the project, as part of initial corpus filtering
rather than this specific review pass — included here because it's the same
kind of check ([08_dedupe_poster_md5.py](../scripts/08_dedupe_poster_md5.py))
and belongs in the same accounting.

## Duplicate detection funnel

- 72 candidate groups found (same title + year + overview, different poster)
- 38 were false positives — one of the two ids was already dead in TMDB
  (404), so there was no real duplicate, just a stale reference
- Of the 34 real duplicates, 18 were resolved (the rest spanned genres
  outside the horror scope above)

## Exact poster duplicates (MD5)

A real example: "Castle Ghosts of Ireland" and "Castle Ghosts of Wales" both
used the exact same poster file (byte-for-byte, same MD5) as "Castle Ghosts
of England" — almost certainly a documentary series where only one episode
ever had unique cover art and the rest were stubbed with a placeholder. Kept
the highest-`vote_count` id in each group, flagged the rest.

## Poster/title mismatch review funnel

Starting from a sample of 8,275 posters reviewed by a vision-LLM (Nova Pro),
547 came back flagged `mismatch`. The "genuinely unexplained" count dropped
at each methodology fix — **none of these were data fixes, all were
matching-logic fixes**:

| Step | Remaining "genuine" mismatches |
|---|---|
| Raw vision-LLM `mismatch` flags | 547 |
| + cross-check against IMDb/TMDB alternate titles | 251 |
| + also check the vision-LLM's own stated reasoning text, not just OCR | 142 |
| + cross-check against 2 more OCR engines | 139 |
| + accent normalization | 137 |
| + whitespace normalization | 135 |
| **+ compare against the catalog's own title/original_title field** | **37** |
| Final: no tool could explain it → excluded | 0 (24 excluded) |

The single largest jump (135 → 37) came from a check that sounds almost too
basic to need calling out: comparing the visible poster text against the
film's *own* catalog title, not only against external alternate-title
databases. A meaningful share of "mismatch" verdicts were the vision model
expressing doubt about genre fit for an obscure title it didn't recognize —
the title text on the poster was correct all along.

## Sample run (`data/sample_output/`), gate ordering in practice

The 100-id sample in this repo includes 6 real ids chosen specifically to
demonstrate each rejection category, plus 94 random ones. Running the full
gate sequence against them is a good illustration of why gate order
matters: **"Castle Ghosts of England/Ireland"** (a real MD5-duplicate pair —
see above) both turned out to *also* have an empty `poster_path` in the
primary metadata, so gate 2 excludes both before gate 8 (MD5 dedup) ever
gets to run on them. Gate 8's own output
(`poster_md5_duplicates.csv`) still shows it correctly detected the
duplicate — it just never became the *final* reason in `excluded_ids.csv`,
because an earlier gate already caught the same row for an unrelated cause.
This is expected, not a bug: whichever gate runs first should win, since
that mirrors what a real sequential pipeline (one that drops excluded rows
between gates, rather than running gates independently over the same input
like this demo does) would actually do.

Final tally for the 100-id sample: **95 validated, 5 excluded** — Edwin
Parker, Castle Ghosts of England, Castle Ghosts of Ireland, and Grudge (all
`no_verifiable_poster`), plus Omegle's cropped-poster duplicate id
(`tmdb_duplicate`).

## Compilation cases, case by case

- **Sheets of Gore** — 6 short films individually listed in TMDB, each with
  the compilation's cover art as its poster. TMDB had a separate, correct
  entry for the compilation itself; collapsed all 6 into that one id.
- **Ultimate Zombie Feast** — same pattern, 2 films → 1 compilation entry.
- **Late Night Horror** (1968 BBC anthology) — 4 episodes shared one poster;
  no dedicated "series" entry existed in TMDB, so the id with the most
  complete metadata (cast/crew count, popularity) was kept as
  representative.
- **Bite Size Halloween** — same shared-poster pattern, but no compilation
  entry existed and no clean tiebreaker was available; left un-collapsed
  by explicit decision rather than picking an id arbitrarily.
- **Ruled out as false positives**: "Teenage Frankenstein" (two genuinely
  different films with reused stock art, different casts), "Ripple" and
  "The Boogeyman" (unrelated films that happen to share a generic title and
  year) — confirmed via director/cast lookup before excluding anything.

## Language detection & translation (gates 5-6), live-verified

`05_comprehend_language.py`/`06_translate_titles.py` chain from
`04_bedrock_ocr.py`'s `text_you_read` — Bedrock's own short title
extraction — not from the real project's `full_ocr` (the longer,
multi-engine OCR blob `poster_title_match.py` actually gates and
translates on). This repo never ported the Textract/EasyOCR/Rekognition
engines that produce `full_ocr` in the first place (`04` is Bedrock-only
by design), so the two pipelines run on genuinely different text, not
just a different model version.

Live-checked (2026-08-15, real AWS calls, `sandbox_bedrock` profile) two
ways, against real ids with known historical Comprehend/Translate
results:

**Fed the port's own default input** (Bedrock's short `text_you_read`)
against 10 real ids: only 3/10 language codes matched exactly. Looked
like model drift at first — it wasn't. The real historical decisions
were made on `full_ocr` text (much longer, much less ambiguous for a
language-ID model) than what Bedrock's title-only extraction gives
Comprehend to work with.

**Re-ran feeding the same `05`/`06` functions the real historical
`ocr_full_text` instead** (no new engines ported — this is
`master_dataset.csv`'s own already-computed column, just plugged into
this repo's live logic in place of Bedrock's shorter extraction): 8/10
language codes matched exactly, and the 2 misses were both very short
`full_ocr` strings (13-16 chars) with low confidence on both sides —
consistent with genuine Comprehend model drift on ambiguous text over
time (the same kind of thing found live-checking Bedrock, see
docs/MODELS.md), not a logic bug. The port's Comprehend call,
translate-gating threshold, Translate call, and overlap scoring are all
faithful to the real project's logic once given comparable input text.

What isn't reproducible: the exact original OCR text some historical
rows were computed from. A few historically-translated ids had a saved
`ocr_full_text` shorter than `TRANSLATE_MIN_CHARS` (60), meaning the
real run's translate gate must have fired on different (likely longer,
possibly a different OCR engine's) text than what ended up in that
column — the same "original bytes aren't reproducible months later"
caveat this project already documents for other scripts (see
poster-metrics-pipeline's docs/RESULTS.md), not a bug in this port.

**Bottom line:** running `04_bedrock_ocr.py` → `05` → `06` chained by
default in this repo validates Bedrock's title reads against
Comprehend/Translate — a real and useful check — but it's a narrower
question than the real project's `full_ocr`-based gates 5-6, and won't
reproduce the "~3,700 of ~65,000 translated" historical count (see
`06_translate_titles.py`'s docstring). Point `--text-col`/`--in` at a
fuller OCR text source if you want the closer comparison; this repo
doesn't provide one by default since it never ported
Textract/EasyOCR/Rekognition.

## Gate 8-9: alternate poster scoring (`11`/`12`) — a correction

The real project's own version of this gate,
`score_multi_poster_variants_ocr.py`, scores TMDB alternate-poster
candidates via **Amazon Rekognition's** `DetectText` — confirmed by
matching its output byte-for-byte against the real
`data/qa/multi_poster_variant_ocr_scores.csv` (806 rows) and
`_swaps.csv` (262 candidates, 78 proposed) files. Earlier drafts of the
public-facing article describing this gate said "Nova Pro" — that's
wrong; searched both the local pipeline checkout and the author's
personal GitHub repo (`juanpduque/what-fear-looks-like`) for a
Nova/Bedrock-based version of this specific gate and found none.

`11_find_alternate_posters.py` (TMDB discovery + download, no AWS,
ports `multi_poster_pipeline.py`'s discover/download commands) and
`12_score_alternate_posters.py` (scoring + swap proposal) are this
repo's port — but `12` sources its OCR read from **Bedrock/Nova Pro**,
not Rekognition, since that's what this repo's gate 5 already uses and
no Rekognition-based alternate-poster script could be located to port
faithfully instead. The *decision logic* is ported as-is from the real
script (same `title_overlap_score`/`title_fuzzy_score` thresholds:
`min_best=0.40`, `min_gain=0.25`, `min_fuzzy_best=0.55`, plus the
"current near zero" rule) — only the OCR source differs. Treat this as
a real, working gate 8-9 with a documented substitution, not a
reproduction of the historical 806/262/78 run.

Live-verified (2026-08-16, real TMDB + Bedrock calls, `sandbox_bedrock`
profile): ran end-to-end against *The Exorcist* (id 9552) and *A
Nightmare on Elm Street* (id 377), 4 real alternate posters each
discovered via TMDB's `images` endpoint. All 8 variants plus both
primaries scored a perfect 1.0 overlap/fuzzy against the catalog title
— expected, not a bug: `11`'s discovery defaults to `en,null` language
posters only (matching the real project's own default), so every
candidate already reads the English title cleanly. 0 swaps proposed for
either film, correctly, since both already had a matching primary
poster — this confirms the pipeline runs correctly end-to-end without
forcing a swap that shouldn't happen; it doesn't yet demonstrate a real
`propose=1` case live (would need a film with a genuinely mismatched
current primary and a better-reading alternate on hand).
