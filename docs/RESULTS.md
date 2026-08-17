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
poster.

**Full run against all 262 real candidates, first attempt (2026-08-16,
retracted -- see correction below).** Ran both engines against the
actual `id`/`title`/`poster_path` rows from the real
`data/qa/multi_poster_variant_ocr_swaps.csv`, TMDB variant discovery via
`11`. Result: real historical run 78 proposed swaps; this port's
`--engine rekognition` 1; `--engine bedrock` 9 -- with **zero ids in
common** between the two engines' proposals. First read: "the discovery
step (`11`) must be finding a narrower set of candidate posters than
the real project's own `multi_poster_pipeline.py`."

**That read was wrong, and the actual bug is worth documenting.** The
`poster_path` fed into that run came from `master_dataset.csv` --
which, it turns out, already reflects the real project's own
`apply_multi_poster_ocr_swaps.py` having run. Checked all 78 real
historical `propose=1` rows against that `poster_path`: **78 of 78**
had the historically-proposed "best alternative" already sitting as
the *current* primary poster in the input. The run wasn't testing "does
this port find the same swaps live" -- it was comparing an
already-corrected poster against itself, which trivially finds nothing
left to fix. A real methodological bug in this port's own test setup,
not a finding about TMDB drift, discovery coverage, or either OCR
engine.

The actual pre-swap `poster_path` does still exist: `data/qa/
poster_title_mismatch_consensus.csv` (306 rows, the original consensus
input the real scoring script's candidates were drawn from, predating
any swap) has it for 252 of the 262 -- confirmed against the RISEN
example above: `poster_path` there is `/vX3uVYYG1YMbZijRoaFSXtNuIO2.jpg`,
not the `mULRjy8rGqZx9Ql5TTKV4inH9If.jpg` that `master_dataset.csv`
gives (which is exactly the file the real run proposed swapping *to*).
96 of the 262 (37%) have a `poster_path` here that differs from
`master_dataset.csv`'s -- this bug wasn't confined to the 78 swapped
rows.

**The corrected re-run (2026-08-16, a fresh AWS sandbox account, real
Bedrock + Rekognition calls).** `data/gate89_full/input_262_corrected.csv`
(252 of the 262 real candidates -- 10 dropped, no recoverable pre-swap
`poster_path` in the consensus file) was scored with both engines against
the same real TMDB variant catalog already cached locally
(`data/gate89_full/catalog.csv`/`posters_multi/`, no re-discovery needed
-- TMDB poster variants don't change on the timescale of a week).

| | proposed swaps |
|---|---|
| Real historical run (262 candidates) | 78 |
| This port, `--engine rekognition` (252 candidates) | 73 |
| This port, `--engine bedrock` (252 candidates) | 77 |

**68 of the 73 Rekognition proposals and 68 of the 77 Bedrock proposals
are the same ids** -- 88%/88% agreement between the two engines. Compare
to the first (retracted) attempt above, which had **zero ids in
common** between the two engines on the same candidate set -- that
alone is a strong independent confirmation the corrected input fixed
the real bug, not just a coincidence of different numbers. All three
counts (78 real, 73 Rekognition, 77 Bedrock) now sit in the same
ballpark, which is what you'd expect once the test actually measures
"does this poster's title match" instead of comparing an
already-corrected poster against itself.

The remaining disagreement (9 Bedrock-only, 5 Rekognition-only, out of
252) is exactly the kind of engine-specific drift this document already
found directly in gate 5's 4-engine comparison -- not a new finding, a
consistent one.

## Gate 5's 4-engine example, re-run live: OCR engines drift too

The article's gate 5 case study ("don't trust a single OCR engine")
compares four real engines' historical reads of three real posters —
*Who Goes There?* (id 752443), *Dykefoot* (644460), *Living
Arrangements* (751497). Live-checked (2026-08-16, real AWS calls) what
Rekognition and Textract read on the *exact same three image files*
today, plus a fresh EasyOCR pass (local, no AWS) and a fresh Bedrock
read. Not just "the numbers moved a little" — the qualitative story
changed, in both directions:

**Who Goes There? (752443)** — the article's clean-vs-messy example.
Historically: Textract read `WHO GOES THERE?` alone, cleanly, at 0.9999
confidence, ignoring the tiny stylized cast-credit text that confused
every other engine. Live today: Textract reads the cast credits too —
`NINA | SIRI | RIKKE | AND LIAM | YNDIS | MELAND | HAUGHEM | MCMAHON |
WHO GOES | THERE? | NFTS` at 0.9645 — correctly, but no longer the
clean single-title read the article's narrative leans on. Rekognition
live reads the same real cast names Textract now picks up, still with
garbage after. EasyOCR live also reads the credits cleanly (`YNDIs
'MELAND HAUGHEM McMAHON WHO GOES THERE?`, conf 0.735, up from a garbled
0.43 historically). Bedrock live: `who goes there?`, `match` — no
numeric confidence field in Bedrock's own output (see the note below).

**Dykefoot (644460)** — engines swapped which one is reliable.
Textract: from a weak-but-plausible "Dyke...dyketect.com" (0.46) to
outright garbage "YOU -" (0.19) — got worse. Rekognition: from
hallucinating Arabic script and a wrong domain ("dykotoot.com", 0.24)
to a clean, correct read ("glitterdrop | dykefoot.com", 0.97) — got
much better. Whichever engine "won" this poster in the original
comparison would not win it today.

**Living Arrangements (751497)** — both still weak, different specific
errors. Textract: "4\nARRANGEMENTS" (0.61) live is `PO` (0.12) — much
worse. Rekognition: comparably messy both times, different exact typos
(`FRODUCTIONS`→`PRODUCTIONS` now correct, `LIVING.`→`IVING` now missing
a letter).

**Bottom line:** this isn't just Bedrock/Comprehend that drift over
time (see this doc's gates-5-6 section above and the sibling repo's
docs/MODELS.md) — Textract and Rekognition, AWS's more "traditional"
(non-LLM) OCR services, also produce measurably different reads on
identical images months apart. Two of three posters got a *better*
overall read live than historically; one engine (Textract) got
meaningfully worse on two of three. No engine is safe to treat as a
frozen, reproducible reference — the "don't trust a single OCR engine"
lesson extends to "don't trust that any given engine's *historical*
read still describes what it does today."

**Separately:** live-checking this surfaced that Bedrock's real output
schema (`text_you_read`/`verdict`/`reason`, see 04_bedrock_ocr.py's
PROMPT) has no numeric confidence field at all. If an article draft
cites a specific "Bedrock confidence" score, that number isn't coming
from this call — worth tracing to its actual source before publishing.

## Poster-type human review: is a zero-OCR-text poster even a real poster?

Follow-up to gates 5-6's finding above: 2,630 of 65,107 real titles
(4.04%) have `ocr_chars == 0` in `poster_title_match.csv` — no text
detected by Rekognition's `DetectText` at all. Zero text is a *candidate*
signal an image isn't actually a movie poster (a film still, a generic
photo, a placeholder), not proof — plenty of real posters are
legitimately textless (minimalist international releases).

2,539 of those 2,630 (the ones with a resolvable `poster_path`) were
reviewed blind by hand via `scripts/qa/build_poster_type_review_page.py`
— see `data/ground_truth/poster_type_human_labels.csv` for the raw
result. **2,538 of 2,539 reviewed:**

| Verdict | Count | % |
|---|---|---|
| No es poster | 1,856 | 73.1% |
| Es poster | 672 | 26.5% |
| No estoy seguro | 10 | 0.4% |

The "zero OCR text" signal holds up: nearly three-quarters of this sample
really aren't movie posters. The other real thing it surfaces: of the 672
confirmed real posters, **670 (99.7%) were also marked as having visible
text Rekognition missed entirely** — reinforcing gate 5's "don't trust a
single OCR engine" finding at a different scale (Rekognition's
`DetectText` specifically, not just "OCR in general," and specifically on
its false-*negative* rate, not just misreads).

Cross-checked against `poster-metrics-pipeline`'s existing `painted`
classifier (illustrated/painted art vs. photographic, `data/medium.csv`):
**no real discriminative value here** — `painted=1` appears in 17.0% of
`es_poster` rows vs. 11.4% of `no_es_poster` rows, both far below what
would make it a useful filter, and predicting "not a poster" from
`painted=0` alone gets ~74.7% precision against a ~73.1% base rate —
essentially no lift over guessing the majority class.

Two patterns that do hold: `no_es_poster` skews toward recent titles
(68% are 2010s or later, vs. 58% for `es_poster`) — small/obscure recent
indie or festival titles are more likely to have never had real poster
art. And Japanese-language titles are disproportionately `es_poster`
(26.8% of that group) vs. `no_es_poster` (6.9%) — consistent with
Rekognition's OCR miss rate being worse on non-Latin scripts specifically.

## Gate 13: content moderation, live-verified

Found while manually reviewing the poster-type sample (above): some real
posters in the corpus sit right at the edge of graphic gore, closer to
shock/snuff imagery than mainstream horror marketing art. The real
project already built and ran two independent signals for exactly this —
`nova_poster_enrich.py` (Nova vision-LLM scoring `blood_gore`/`violence`/
`sexual_content` 0-1 per poster) and `rekognition_enrich.py` (Amazon
Rekognition's purpose-built `detect_moderation_labels` API) — both
present as real columns in `master_dataset.csv` (`nova_blood_gore`,
`rek_gore`, `rek_mod`, etc.), just never ported into this repo's own gate
structure until now. `13_content_moderation.py` ports both faithfully,
using the real project's own real thresholds: 0.5 for Nova's fields
(`nova_enrich_live_summary.py`'s own `pct_ge(col, thr=0.5)` corpus
reporting), 0.4 for Rekognition's (`rekognition_enrich.py`'s own
`decade_summary` `mean_flag(key, thr=0.4)`).

**Live run (2026-08-16, real Nova + Rekognition calls, 20 concurrent
workers, `sandbox_bedrock` profile):** all 672 ids from the poster-type
human review (above) marked `es_poster` — scoring only confirmed real
posters, not the 1,856 non-poster images already excluded by that same
review. **74 of 672 (11.0%) flagged** by at least one engine crossing its
real threshold. 0 errors across 672 real API calls. Reason breakdown:
`nova_blood_gore` 44, `rek_violence` 38, `nova_violence` 35, `rek_gore`
31, `rek_nudity` 6, `nova_sexual_content` 2 (a flagged row often trips
more than one reason).

The highest-severity real result is *Nekromantik 2* (id 48636) — a real,
notoriously extreme film, flagged by both engines on every relevant
axis (`nova_blood_gore`, `nova_violence`, `rek_gore`, `rek_violence` all
≥ threshold). Other real top hits (*Wax*, *Meat*, *Red Account: My Bloody
Angel*, *The Rope Maiden* — nudity + gore) are all plausible, not noise.

One real, faithful extension beyond what the real project's script ever
did: Rekognition's `detect_moderation_labels` response already includes
"Explicit Nudity"/"Suggestive" labels — `rekognition_enrich.py` only ever
parsed out Violence/Gore/Weapons. Extracting them the same way
(`rek_nudity`/`rek_suggestive`) fired for real on 6 of the 74 flagged
rows, including catching a case (*The Rope Maiden*) where nudity was the
only additional signal beyond gore already present.

TMDB's own `adult` field, checked against the full corpus
(`master_dataset.csv`, 145,128 rows): `False` for 144,974 of them,
blank for the other 154 — essentially no variance, not usable as a
cross-check signal for this corpus. IMDb's `isAdult` field wasn't
checked this session; a live cross-reference (same free `title.basics.tsv.gz`
dataset gates 5-6's genre classifier validation already uses) is a
reasonable next step if TMDB's flag being empty turns out to matter.

## Why Rekognition misses text that's really there — and what reads it correctly

Live-verified 2026-08-16 against 5 real posters from the poster-type
review's `has_text=si` group (real text a human confirmed, that
Rekognition's `detect_text` still scored `ocr_chars==0` on): two distinct,
separate failure modes, not one.

**Non-Latin script — Rekognition sees nothing at all.** Tamil
("Chandramukhi", id 53122) and Chinese ("A Dead Man Visits the Living",
id 285981) returned **zero** `TextDetections`, not even low-confidence
noise. `detect_text` is tuned for Latin script and doesn't attempt these.

**Stylized fonts — Rekognition reads something, wrong, at low confidence.**
"Mary Reilly" (id 9095, English, gothic title treatment) → *"Mery Radly"*
at 14.9% confidence. "Ringu 2" (id 9669, Japanese poster with a Latin
sub-title) → *"INC,"* at 47.5%. "Cadaver" (id 55142, Thai) → a stray *"@"*
at 15.4%. Any real confidence-threshold filter discards these the same as
a true miss.

**A general vision-LLM (Nova, via this repo's own `04_bedrock_ocr.py`,
already built for gate 4) reads 4 of these 5 correctly**, live-verified
against the identical images: "Mary Reilly" exact match; "Chandramukhi"
transliterated correctly (`text_you_read: "chandramukhi"`); "Ringu 2" read
the real printed Latin sub-title "RING" (a legitimate catalog-vs-poster
title difference, not an OCR failure); "Cadaver" read real Thai
characters (didn't match the English catalog title, but it was real Thai
text, not noise). Only the Chinese poster came back empty for Nova too.
Nova's own `verdict` field measures title-*match*, not text-*presence* —
for a "does Rekognition's zero-OCR mean this genuinely has no text"
check, read `text_you_read != ""`, not `verdict`.

## Gate 4 (Nova OCR) and gate 13 (moderation), run live at full corpus scale

Both gates were re-run against all 131,644 `master_dataset.csv` rows with
a `poster_path` (not just samples), 20-40 parallel workers,
`sandbox_bedrock` profile, 2026-08-16. Also added the same treatment to
**Pixtral Large** (`us.mistral.pixtral-large-2502-v1:0`) via gate 4's
`--model` override — this repo's own OCR bake-off (in the real project's
`pipeline/` root: `pilot_ocr_*.py`, `ocr_metrics.py`,
`summarize_ocr_pilot_v2.py`) had already declared Pixtral the real winner
(0.939 general / 0.801 hard-set title-overlap score vs Rekognition's
0.849 / 0.454) but that winner had *never* been run past a ~100-poster
pilot sample before this. Full numbers pending completion (all three were
still running in background as of this write-up); see the repo's
background-task history for final counts.

## Chaining gates 4→5→6 for a real English translation of what Nova reads

`04_bedrock_ocr.py`'s `text_you_read` is a raw visual reading, not a
translation — live-verified across the 2,539 `poster_type_sample.csv` set
that this genuinely varies by case: Tamil got phonetically transliterated
("chandramukhi"), a Japanese poster's real printed Latin sub-title got
read as-is ("RING"), and Thai got transcribed in the original script
verbatim. None of that is English *meaning*. Chaining `05_comprehend_language.py`
(language ID) → `06_translate_titles.py` (Amazon Translate, real machine
translation, not an LLM guessing) onto Nova's output gets real English
translations: e.g. `淫屍戲血` → "Zombie Bloods" (id 40351), `鬼見你` →
"Ghost See You" (id 145996), `سفير الجحيم` → "The Ambassador of Hell"
(id 514101), `목두기 비디오` → "Neckline video" (id 450934).

Two real gaps found and fixed getting a clean result from this chain:

1. `06_translate_titles.py`'s `TRANSLATE_MIN_CHARS=60` gate is calibrated
   for the real project's long multi-field `full_ocr` text. Chained onto
   Nova's short title-only `text_you_read` instead, it's not just "fewer
   rows qualify" as the module's own docstring anticipated — it's **zero**
   (no movie title reaches 60 characters). Added an opt-in `--min-chars`
   override; default behavior for the real `full_ocr` use case is
   unchanged.
2. `translate_text` crashed the whole run on
   `UnsupportedLanguagePairException` (Odia "or" and Malagasy "mg" aren't
   translatable-from pairs Amazon Translate supports) — 31 real
   translations in, no cached progress lost, but a manual restart
   required. Now caught and recorded in a new `error` column instead.

Final result on the full 2,539-row set: **245 real translations, 2
real Translate-side errors (recorded, not crashes), 0 script errors.**
