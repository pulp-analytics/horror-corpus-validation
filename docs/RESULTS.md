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
kind of check ([09_dedupe_poster_md5.py](../scripts/09_dedupe_poster_md5.py))
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
primary metadata, so gate 3 excludes both before gate 9 (MD5 dedup) ever
gets to run on them. Gate 9's own output
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

`06_comprehend_language.py`/`07_translate_titles.py` chain from
`05_bedrock_ocr.py`'s `text_you_read` — Bedrock's own short title
extraction — not from the real project's `full_ocr` (the longer,
multi-engine OCR blob `poster_title_match.py` actually gates and
translates on). This repo never ported the Textract/EasyOCR/Rekognition
engines that produce `full_ocr` in the first place (`05` is Bedrock-only
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

**Re-ran feeding the same `06`/`07` functions the real historical
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

**Bottom line:** running `05_bedrock_ocr.py` → `05` → `06` chained by
default in this repo validates Bedrock's title reads against
Comprehend/Translate — a real and useful check — but it's a narrower
question than the real project's `full_ocr`-based gates 5-6, and won't
reproduce the "~3,700 of ~65,000 translated" historical count (see
`07_translate_titles.py`'s docstring). Point `--text-col`/`--in` at a
fuller OCR text source if you want the closer comparison; this repo
doesn't provide one by default since it never ported
Textract/EasyOCR/Rekognition.

## Gate 8-9: alternate poster scoring (`12`/`13`) — a correction

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

`12_find_alternate_posters.py` (TMDB discovery + download, no AWS,
ports `multi_poster_pipeline.py`'s discover/download commands) and
`13_score_alternate_posters.py` (scoring + swap proposal) are this
repo's port — but `13` sources its OCR read from **Bedrock/Nova Pro**,
not Rekognition, since that's what this repo's gate 6 already uses and
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
— expected, not a bug: `12`'s discovery defaults to `en,null` language
posters only (matching the real project's own default), so every
candidate already reads the English title cleanly. 0 swaps proposed for
either film, correctly, since both already had a matching primary
poster.

**Full run against all 262 real candidates, first attempt (2026-08-16,
retracted -- see correction below).** Ran both engines against the
actual `id`/`title`/`poster_path` rows from the real
`data/qa/multi_poster_variant_ocr_swaps.csv`, TMDB variant discovery via
`12`. Result: real historical run 78 proposed swaps; this port's
`--engine rekognition` 1; `--engine bedrock` 9 -- with **zero ids in
common** between the two engines' proposals. First read: "the discovery
step (`12`) must be finding a narrower set of candidate posters than
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
found directly in gate 6's 4-engine comparison -- not a new finding, a
consistent one.

## Gate 6's 4-engine example, re-run live: OCR engines drift too

The article's gate 6 case study ("don't trust a single OCR engine")
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
schema (`text_you_read`/`verdict`/`reason`, see 05_bedrock_ocr.py's
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
text Rekognition missed entirely** — reinforcing gate 6's "don't trust a
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

**Still an open gap** (confirmed live, 2026-08-17, spot-checking a
different sample entirely): no gate in this repo actually filters on
this signal. [Gate 3](../scripts/03_verify_poster_exists.py) only checks
that `poster_path` resolves with an HTTP 200 -- it verifies the image is
*reachable*, not that it depicts real poster art. A concrete example
from this session's `id=1459576` ("hey,"): `ocr_n_lines=0`,
`title_ocr_qa_verdict=no_title_on_poster`, and it's already labeled
`no_es_poster` in this exact human-reviewed ground-truth file -- yet it
passes gate 3 (and every gate after it) cleanly, because none of them
check poster *type*, only existence/dedup/moderation. Building a gate
that acts on this would need to accept the tradeoff quantified above (a
hard zero-OCR filter costs the 26.5% of zero-OCR posters that are real,
legitimately textless releases) -- not done yet, flagged here as a real,
already-quantified gap rather than a new one.

## Gate 14: content moderation, live-verified

Found while manually reviewing the poster-type sample (above): some real
posters in the corpus sit right at the edge of graphic gore, closer to
shock/snuff imagery than mainstream horror marketing art. The real
project already built and ran two independent signals for exactly this —
`nova_poster_enrich.py` (Nova vision-LLM scoring `blood_gore`/`violence`/
`sexual_content` 0-1 per poster) and `rekognition_enrich.py` (Amazon
Rekognition's purpose-built `detect_moderation_labels` API) — both
present as real columns in `master_dataset.csv` (`nova_blood_gore`,
`rek_gore`, `rek_mod`, etc.), just never ported into this repo's own gate
structure until now. `14_content_moderation.py` ports both faithfully,
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

**A general vision-LLM (Nova, via this repo's own `05_bedrock_ocr.py`,
already built for gate 5) reads 4 of these 5 correctly**, live-verified
against the identical images: "Mary Reilly" exact match; "Chandramukhi"
transliterated correctly (`text_you_read: "chandramukhi"`); "Ringu 2" read
the real printed Latin sub-title "RING" (a legitimate catalog-vs-poster
title difference, not an OCR failure); "Cadaver" read real Thai
characters (didn't match the English catalog title, but it was real Thai
text, not noise). Only the Chinese poster came back empty for Nova too.
Nova's own `verdict` field measures title-*match*, not text-*presence* —
for a "does Rekognition's zero-OCR mean this genuinely has no text"
check, read `text_you_read != ""`, not `verdict`.

## Gate 5 (Nova OCR) and gate 14 (moderation), run live at full corpus scale

Both gates were re-run against all 131,644 `master_dataset.csv` rows with
a `poster_path` (not just samples), 20-40 parallel workers,
`sandbox_bedrock` profile, 2026-08-16. Also added the same treatment to
**Pixtral Large** (`us.mistral.pixtral-large-2502-v1:0`) via gate 5's
`--model` override — this repo's own OCR bake-off (in the real project's
`pipeline/` root: `pilot_ocr_*.py`, `ocr_metrics.py`,
`summarize_ocr_pilot_v2.py`) had already declared Pixtral the real winner
(0.939 general / 0.801 hard-set title-overlap score vs Rekognition's
0.849 / 0.454) but that winner had *never* been run past a ~100-poster
pilot sample before this. Full numbers pending completion (all three were
still running in background as of this write-up); see the repo's
background-task history for final counts.

## Chaining gates 4→5→6 for a real English translation of what Nova reads

`05_bedrock_ocr.py`'s `text_you_read` is a raw visual reading, not a
translation — live-verified across the 2,539 `poster_type_sample.csv` set
that this genuinely varies by case: Tamil got phonetically transliterated
("chandramukhi"), a Japanese poster's real printed Latin sub-title got
read as-is ("RING"), and Thai got transcribed in the original script
verbatim. None of that is English *meaning*. Chaining `06_comprehend_language.py`
(language ID) → `07_translate_titles.py` (Amazon Translate, real machine
translation, not an LLM guessing) onto Nova's output gets real English
translations: e.g. `淫屍戲血` → "Zombie Bloods" (id 40351), `鬼見你` →
"Ghost See You" (id 145996), `سفير الجحيم` → "The Ambassador of Hell"
(id 514101), `목두기 비디오` → "Neckline video" (id 450934).

Two real gaps found and fixed getting a clean result from this chain:

1. `07_translate_titles.py`'s `TRANSLATE_MIN_CHARS=60` gate is calibrated
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

## Poster MD5 dedup at real full-corpus scale — 113 groups, 366 ids

Live-verified 2026-08-16: `09_dedupe_poster_md5.py` (given `--shard-index`/
`--shard-count`, added this session) run against all 131,644
`master_dataset.csv` rows with a `poster_path`, 20 parallel shards for the
download+hash phase, merged, then a single final group-by-md5 pass.
**Definitive result: 113 exact-duplicate-poster groups, 366 ids.**

That's close to, but not identical to, the 113-group/372-id count a
cheap `poster_path` exact-string-match found first (no downloads, just
comparing the column) — and the 6-id gap is two genuinely different real
findings, not noise:

- **4 ids the string-match structurally cannot find**: "Behind The
  Screen" (1456954), "untitled" (1192817), "Godspeed" (1258187), "Scent"
  (1449322) — four different `poster_path` values, but the exact same
  real image bytes (md5 `f01e49ef...`) once actually downloaded. This is
  the reason to hash bytes instead of trusting the path string: TMDB can
  serve the same image asset from more than one path.
- **10 ids the string-match wrongly grouped**: e.g. "The Midnight
  Express" (954359) and "The Menace" (954363) share the *exact same*
  `poster_path` string in `master_dataset.csv`
  (`/qqUnO5D9ZJi0w2GYRYKvfYYyXJ2.jpg`) but hash to two different real
  MD5s once downloaded live. Not a download failure (both cached
  successfully with a real, non-empty hash) — the path stored in the
  historical corpus no longer serves the same bytes TMDB's CDN returns
  today. A real case of the historical snapshot drifting from current
  CDN state, not a bug in either script.

Confirms the specific case that started this check: ids 1548140, 1548141,
1548185 ("Fake Documentary Q" franchise entries) all hash to the same
real MD5, `keep=1` correctly assigned to 1548141 via the completeness
cascade (imdb_id > credits > trailer > popularity).

Two real bugs found and fixed getting a clean run at this scale (both
also fixed this session, see git log for `09_dedupe_poster_md5.py` and
`utils/tmdb_client.py`): `tmdb_get()` had no retry and crashed the whole
run on one transient TMDB timeout; an errored poster-hash row was
silently treated as "done" forever on resume instead of being retried,
permanently under-counting whatever group that id belonged to.

## IMDb `isAdult` — real signal, unlike TMDB's, but measuring something different

TMDB's own `adult` field (checked earlier) has essentially no variance
(99.9% `False` across the full corpus) and isn't usable. IMDb's
`isAdult`, cross-referenced live 2026-08-16 against the free
`title.basics.tsv.gz` bulk dataset (225MB, all of IMDb, not a per-request
API) for all 103,625 `master_dataset.csv` rows with a real `imdb_id`:
**160 real hits (0.155%)** — genuine exploitation/erotic-horror titles
(the *Emanuelle* series, *Urotsukidōji*, *La Blue Girl*), not noise.

Cross-checked against gate 14's content-moderation flags as they became
available (60/160 processed at check time): **22/60 (36.7%) were also
flagged** by gate 14's own visual poster analysis — meaningfully above
the corpus's overall flag rate (~20%), a real ~1.8x lift. But most
(63%) weren't caught visually at all: `isAdult` is a *film-level* flag
(explicit content in the actual runtime), not a *poster* one — plenty of
exploitation films use suggestive-but-not-explicit poster art. The two
signals are complementary, not redundant; neither should stand in for
the other.

## Isolated prompts vs. one combined mega-prompt — resolved with real human review

Real design question raised mid-session: this repo's gates ask Nova one
narrow question per call (04 = title text only, 13 = moderation only).
The real project's `nova_poster_enrich.py` asks 15+ things in one call
instead. Does the isolated design actually produce better results, or is
it just extra API calls for no real gain? Tested live 2026-08-16, same
model (Nova Pro), same 672 posters, only the prompt structure differs
(`scripts/qa/nova_mega_prompt_comparison.py`, `ENRICH_PROMPT` copied
verbatim from the real project).

**Title reading** (real ground truth: catalog title overlap score):
isolated 0.497 mean overlap vs. combined 0.269 — isolated wins on 181/658
posters, combined on 11, the rest ties (mostly cases neither engine could
read at all). Isolated reads titles roughly twice as accurately.

**Moderation** (blood_gore/violence/sexual_content): the two prompts
disagreed on the >=0.5 flag decision for 155/658 posters. No ground
truth existed for this, so a blind human-review tool was built
(`scripts/qa/build_mega_prompt_review_page.py` — shows only the poster
and a direct yes/no/unsure question per disputed axis, without revealing
either system's score, to avoid anchoring the reviewer) and reviewed by
hand. Important calibration note from that review: "yes" was judged as
*does this look genuinely photorealistic/extreme*, not *does the artwork
contain any stylized reference to blood/violence* — horror poster art
routinely includes illustrated blood as a normal genre convention, and
that's specifically not what this project wants flagged (see the
gore/snuff-exclusion goal this whole gate 14 effort started from).
Result, matched against that human judgment:

| axis | isolated (gate 14) correct | combined (mega-prompt) correct |
|---|---|---|
| blood_gore | 85.5% (65/76) | 14.5% (11/76) |
| violence | 87.9% (94/107) | 12.1% (13/107) |
| sexual_content | 72.2% (39/54) | 27.8% (15/54) |

Since every row here is a disagreement by construction (one engine says
flagged, the other doesn't), exactly one side matches the human verdict
per row -- these percentages are directly complementary, not independent
error rates. **Isolated wins by 3-6x across every axis tested.** A
plausible mechanism: AWS's own moderation labels (Rekognition, inside
gate 14's isolated design) are trained on real photographs, so they
already skew toward flagging photorealistic content over stylized
illustration; Nova given one narrow question per call seems to inherit
that same discipline, while the same model asked 15 things in one call
appears to interpret "blood_gore likelihood" more literally, catching
stylized genre-typical horror art the human reviewer correctly didn't
want flagged.

Side finding: the real `ENRICH_PROMPT`, copied verbatim for this test,
reproduces the exact "literal 'none' echoed as a sensitive-content tag"
bug this repo found and fixed in its own early gate 14 draft (see
`14_content_moderation.py`'s prompt-wording fix, live-verified
2026-08-15) — meaning that noise is likely present in the real corpus's
actual historical moderation data too, not just an artifact of this
repo's first attempt.

## Gate 10 (compilation collapse) at real scale — 67/110 groups rescued

Rather than wait for the full-corpus Nova OCR cascade (hours away),
gate 10 was fed the exact real universe of shared-poster candidates
already known with certainty from the full-corpus MD5 dedup (113
groups/366 ids) — ran `05_bedrock_ocr.py` fresh on just those 366 (a few
minutes) to get real OCR text, merged it back with `poster_path` (gate 5's own output doesn't carry that column through — a real chaining bug
caught live: first attempt found "0 groups, 0 ids" because of it), then
ran `10_collapse_compilations.py` for real.

Live result 2026-08-16: **110 groups, 362 ids** (close to but not
identical to the MD5 dedup's 113/366 -- a handful of ids' OCR text came
back empty/errored and slightly changed grouping, not investigated
further given the close match). Of those, **67 groups (61%) had a
rescuable compilation entry in TMDB** -- confirms the "Fake Documentary Q"-
style pattern (segments sharing a poster, collapsible into one real
compilation/anthology entry) generalizes real. The other 43 groups have
no compilation entry in TMDB and are reported unresolved, same as the
script's own documented behavior -- needs a human call (exclude vs.
leave as-is), not something to auto-resolve.

## OMDb's Rated (MPAA/TV) vs. gate 14's visual flag — a clean, monotonic gradient

With OMDb enrichment complete (103,625/103,625 ids) and gate 14's full-
corpus run 59% done (77,825/131,644 as of this check), cross-referenced
`Rated` against `flagged` for the 28,800 ids with both a real rating and
a gate 14 result:

| Rated | flagged % |
|---|---|
| G / TV-G / TV-PG (children's) | 3.1-7.7% |
| PG / PG-13 / TV-14 / Approved | 10.8-14.6% |
| R / TV-MA / Unrated / Not Rated | 21.5-26.9% |
| X / NC-17 | 36.7-37.5% |

A clean, close-to-monotonic gradient across the real severity scale --
about 5x higher flag rate at X/NC-17 than at G-rated. Unlike the
`isAdult` check (binary, 0.155% of the corpus, hard to use as a
continuous signal), MPAA/TV ratings span the full severity range and
line up sensibly with gate 14's own visual scoring, a real independent
validation that the visual moderation gate is measuring something real
and coherent, not noise. Will get more complete as gate 14's full run
finishes.

## Refining gate 14's prompt with the photorealism-vs-stylized distinction

Given the human review above explicitly judged "genuinely photorealistic/
extreme," not "any stylized reference to blood/violence" (illustrated
horror-poster gore is normal genre convention this project doesn't want
flagged), `MODERATION_PROMPT` in `14_content_moderation.py` was rewritten
to say that explicitly: score low for illustrated/stylized art, high only
for photorealistic/graphic depiction. Live-verified 2026-08-16 by
re-scoring the same 155 human-reviewed posters with only the prompt
changed (Nova-only, same model, Rekognition untouched):

| axis | old prompt | new prompt |
|---|---|---|
| blood_gore | 85.5% (65/76) | 85.5% (65/76) |
| violence | 87.9% (94/107) | 89.7% (96/107) |
| sexual_content | 72.2% (39/54) | 72.2% (39/54) |

Honest result: barely moved the needle -- no regression, a marginal
+1.8pp on violence, flat elsewhere. The isolated prompt was already
implicitly good at this distinction even without saying so explicitly,
consistent with the finding above: asking about only 3 things per call
(vs. the mega-prompt's 15) appears to be what actually drives the
discernment, not this specific wording. Kept as the new default anyway
-- no downside, and the intent is now explicit in the prompt for future
maintainers instead of relying on the model happening to infer it.

## OMDb enrichment — a new, independent data source (not a re-port)

Unlike the gates above, OMDb was never part of the real project's
methodology — this is new work. Added `enrich_omdb.py` (in the real
pipeline, not this repo, since it needs `master_dataset.csv`'s
`imdb_id`) to pull `Rated` (MPAA), Rotten Tomatoes score, Metacritic
score, IMDb rating/votes, genre, plot, and an independent poster URL for
all 103,625 rows with a real `imdb_id`, via OMDb's real API
(`omdbapi.com`, a paid $1/month Patreon tier unlocking 100k req/day --
the free 1k/day tier is impractical at this corpus's scale). Live-tested
against real titles (e.g. *Star Wars*: Rated PG, RT 93%, Metacritic
90/100, imdbRating 8.6) before running at full scale. Results pending as
of this write-up; see `pipeline/data/qa/omdb_enrichment.csv` once the
background run completes.

## Bedrock throttling: Pixtral's real per-account limit is much lower than Nova's

Live-discovered (2026-08-17, `sandbox_bedrock` profile) running gate 5's
full-corpus cascade with both engines: Nova Pro tolerated 20 parallel
shards cleanly (480 real errors / 131,644 = 0.4%). Pixtral Large did not
-- the identical 20-shard pattern produced 91% `ThrottlingException`.
Reducing to 4 shards still produced 84-92% throttling in the most
recently-written rows (not just stale rows from the first attempt --
confirmed via a dedicated recent-error-rate check, see below). A direct
rate probe settled it: serial (1 request at a time), `--delay 1.5`
still throttled ~84% of calls; `--delay 2.5` and `--delay 5.0` both ran
clean (0/30 and 0/12 errors). This isn't a concurrency bug in this repo's
code -- `utils/aws_config.get_client()` already configures
`Config(retries={"max_attempts": 5, "mode": "adaptive"})` -- it's a real,
low, per-account Bedrock quota for this specific model in this specific
sandbox, well below what a naive "spread the same delay across N
parallel shards" assumption would predict. Anyone running gate 5 with
`--model` pointed at Pixtral (or any model without a known-good
concurrency budget) should rate-probe with a small `--limit`-equivalent
sample first rather than assuming Nova's tolerance carries over.

**A stall-only watchdog missed this for a full run's worth of wall-clock
time.** The failure mode -- a process alive and steadily writing output,
where most of what it writes is an error row -- looks identical to a
healthy run to any monitor that only checks "is it still writing?" (log
mtime, row-count-increasing). It was only caught by manually inspecting
the `error` column after a run finished. The fix: a monitor that
periodically re-reads the actual output and computes the error rate
among the most-recently-written rows (not cumulative-since-start, which
stays polluted by an earlier failed attempt long after a fix lands) and
alerts on a threshold crossing -- the same class of check `--retry-errors`
already assumes exists somewhere, just automated instead of manual.

## Session credentials expiring mid-run

A `sandbox_bedrock` STS session expired partway through the Pixtral
retry above, producing a second wave of `ExpiredTokenException` errors
indistinguishable in the output CSV from the throttling errors already
there. `--retry-errors`'s existing contract (treat any row with a
non-empty `error` as not-done, append a fresh attempt on the next run)
absorbed this correctly with zero data loss once the session was
refreshed (`aws configure set ... --profile sandbox_bedrock` from a
fresh Workshop Studio credential export, verified with
`aws sts get-caller-identity` before resuming) -- worth calling out
explicitly as the reason no gate in this repo should ever overwrite
existing output rows in place instead of appending.

## Coverage-audit methodology: catching a silent partial-merge bug

Found live in the private pipeline's `build_master_dataset.py` (not
this repo, but the same class of bug this repo's own scripts are
equally exposed to): the OCR merge step correctly looped over every
genre-coverage variant file (`poster_ocr_rek_text{_scifi,_mystery,
_thriller,_alllang}.csv`), but two sibling merge steps (`faces_v2.csv`,
`poster_title_match.csv`) only ever read the base (horror-only) file --
an oversight, not a deliberate scope decision, that silently left
~52% of a multi-genre corpus's `faces_*`/`title_match_*` columns empty
without ever raising an error, because every individual read succeeded;
the loop just never widened. It surfaced by computing average+minimum
fill-rate per column-name-prefix and flagging any group whose coverage
was suspiciously below the corpus's `poster_path`-verified universe,
then cross-tabulating the missing rows against `sources` (the genre-tag
column) -- the exact-alignment with one tag value (a 99.99%+ match rate)
is what distinguished "silent merge bug" from "intentional scope"
(compare `celeb_*`/`pose_*`, which showed a similarly partial fill rate
but zero correlation with any single tag -- those are correctly scoped
to `n_faces > 0`, not bugged). Worth a standing QA script in any repo
merging several genre/source-tagged variant files into one wide table.

## CLIP same-artwork threshold: the real project's 0.96 was never validated, and it's wrong

`multi_poster_pipeline.py`'s `select` command clusters a movie's TMDB
poster variants by CLIP cosine similarity, `--sim 0.96` by default --
above that, two images are the same underlying artwork (just cropped/
color-adjusted/re-touched); below it, distinct. This default had never
been checked against real human judgment before -- it was ported as-is
from the real project on the assumption it was already correct.

Live human review (2026-08-17), two rounds, 85 real pairs total from
this repo's own real horror-corpus embeddings
(`data/multi_poster_embeddings.npz`, fetched fresh from TMDB for
review): 60 pairs stratified across the 0.94-0.98 boundary plus
clearly-same/clearly-different anchors, then 25 more filling the
untested 0.75-0.94 gap once the first round showed the boundary itself
was miscalibrated. Blind review -- the human reviewer never saw the
similarity score or which side of any threshold a pair fell on.

| threshold | accuracy | precision | recall | false positives | false negatives |
|---|---|---|---|---|---|
| 0.85 | 92.9% | 93.3% | 96.6% | 4 | 2 |
| 0.88 | 94.0% | 98.2% | 93.1% | 1 | 4 |
| **0.90** | 91.7% | **100%** | 87.9% | **0** | 7 |
| **0.96 (real project's default)** | 65.5% | 100% | **50.0%** | 0 | **29** |

**0.96 misses half of all genuinely-same-artwork pairs** -- it never
produces a false positive (never wrongly merges two different posters),
but it wrongly treats a coin-flip's worth of real same-artwork variants
as distinct, inflating `n_clusters`/`n_discarded_variants` and, more
importantly, missing real duplicate-artwork detections a canonical-
poster-selection step exists specifically to catch. **0.90 is the
better default**: still zero measured false positives across the full
85-pair sample (the failure mode this project cares most about avoiding
-- silently merging two actually-different posters), while catching 38%
more of the real matches (87.9% vs. 50.0% recall). Below ~0.85 real
false positives start appearing, so this isn't "lower is just better" --
0.90 is a measured floor, not a guess.

Not yet changed in `multi_poster_pipeline.py` itself (that's the real
project's own file, ported as-is elsewhere in this repo); worth raising
upstream. Any future gate in this repo that ports the `select` /
clustering step should default to `--sim 0.90`, not `0.96`.

## Alt-poster classification: refining `has_credits_text` against human ground truth

A Nova Pro classifier judges each alt-poster variant against a movie's
primary poster on two axes -- `same_artwork` (is it the same underlying
illustration, just cropped/recolored/re-lettered?) and `has_credits_text`
(does the variant carry a real credits block that would disqualify it as
a clean canonical poster?). This is a candidate signal for a future
canonical-poster-selection gate, so before trusting it, it needed the
same treatment as the CLIP threshold above: blind human review on a real
100-pair sample, ground truth the classifier never saw.

The first pass (`same_artwork` 78% precision / 100% recall,
`has_credits_text` only 52% precision / 96.3% recall) showed a specific,
diagnosable failure mode on inspecting the false positives: Nova was
flagging a movie's own stylized title wordmark/logo as a "credits
block" whenever it was large, stacked, or artistically rendered (e.g.
*Spider-Man 3*'s title treatment) -- a title logo is not a credits
block, but the original prompt never said so explicitly.

Fix: rewrote the `has_credits_text` half of the prompt to positively
describe what a real credits block looks like (small print, several of
cast/crew/studio-logo/date/rating/copyright, individually hard to read
at a glance) and explicitly exclude title wordmarks, taglines, and "no
text at all" as `false` regardless of size or styling. Re-ran on the
exact same 100 human-reviewed pairs for a clean before/after against the
same ground truth (`same_artwork` logic untouched, so its numbers are
the noise floor):

| signal | version | accuracy | precision | recall |
|---|---|---|---|---|
| `same_artwork` | v1 | 89.0% | 78.0% | 100% |
| `same_artwork` | v2 (unchanged logic, re-run) | 88.0% | 76.5% | 100% |
| `has_credits_text` | v1 | 75.0% | **52.0%** | 96.3% |
| `has_credits_text` | v2 (refined prompt) | **91.0%** | **80.0%** | 88.9% |

`has_credits_text` went from barely-better-than-a-coin-flip precision
(24 false positives out of 100, mostly title-logo confusion) to 80%
precision with a small, acceptable recall cost (3 new false negatives).
`same_artwork`'s ~1pt swing between v1/v2 is just re-run noise -- its
prompt half wasn't touched, confirming the fix was isolated to the
signal it targeted. This reconfirms the session's repeated finding that
narrow, single-purpose prompts beat combined ones: the original mega-
prompt's `has_credits_text` question was being pulled off-target by the
same image region the `same_artwork` question was also looking at.

Not yet ported into a numbered gate script in this repo -- this was
scored against a hand-picked 100-pair sample, not the full corpus, and
no canonical-poster-selection gate exists yet to consume it. Worth
building as a future gate once the multi-genre CLIP re-cluster (see
above, `--sim 0.90`) lands, using v2's prompt from the start.

## Validating the Nova-mismatch/Translate reclassification without a polyglot reviewer

The Comprehend+Translate check above (20,168 real "mismatch" verdicts
from the full-corpus Nova OCR run, reclassified as
`false_mismatch_language` / `true_mismatch` / `true_mismatch_english` /
`translate_failed`) needed a ground-truth check of its own -- but a
single human reviewer can't personally verify Danish, Turkish, Cyrillic,
or CJK title matches. Rather than restrict review to languages one
person happens to read (which would silently skip validating the exact
cases -- foreign-script titles -- this signal exists to handle),
cross-checked every row against something better than one person's
language knowledge: IMDb's own curated alternate-title data
(`alt_titles_imdb`, already merged into `master_dataset.csv` from
`title.akas.tsv.gz`, populated for 102,891 ids). If the OCR text Nova
read matches ANY of a movie's real, IMDb-recorded regional titles (token
or character-set Jaccard >= 0.5, whichever scores higher -- character
overlap covers CJK, which has no whitespace word boundaries), that's
independent corroboration, checked via a completely different mechanism
than Amazon Translate.

| reclassified bucket | n | confirmed by a real IMDb AKA |
|---|---|---|
| `true_mismatch_english` | 8,219 | 78.4% |
| `false_mismatch_language` | 4,425 | 75.5% |
| `true_mismatch` | 7,372 | **48.5%** |
| `translate_failed` | 152 | 42.1% |

`false_mismatch_language`'s rescue mostly holds up: three-quarters of
the pairs Comprehend+Translate flagged as "actually the real title, just
untranslated" do match a real recorded AKA independently. But the bigger
finding is in `true_mismatch`: **48.5% of the rows kept as genuine
mismatches also match a real IMDb AKA** -- nearly half. This isn't a
contradiction of the original check so much as a scope gap in it: the
Translate step only ever compared the translated OCR text against the
catalog's single primary title, but a movie can have dozens of real
regional titles, and a Danish poster's real Danish title translating
loosely (or not being a literal translation at all) doesn't mean it's
wrong -- it can still match a *different* recorded AKA directly, no
translation needed (e.g. `Dragonflies`'s OCR text `Oyenstikker` scores
0.0 against the English catalog title but 0.8 directly against the
Danish AKA "Øjenstikker" once case/diacritic-folded). Checking against
the full AKA list catches matches that checking against one title can't.

Caveat this cuts both ways: absence of an AKA match isn't proof of a
real mismatch either -- IMDb's AKA list isn't exhaustive, and OCR misreads
reduce token overlap even for genuinely correct titles (spot-checking
the highest-scoring `false_mismatch_language` non-matches shows several
near-misses sitting just under the 0.5 threshold, e.g. `"HASTA LA
PRÓXIMA LUZ del DÍA"` for *Until The Next Daylight* at 0.474 -- almost
certainly a real match the threshold is just barely excluding). This is
directional evidence, not a perfect oracle -- but it's real, independent,
deterministic, doesn't require any reviewer to know 20 languages, and
scales to the full 20,168 rows for near-zero cost (no LLM calls).

**Actionable finding**: any future re-run of this reclassification
should compare translated/OCR text against the FULL `alt_titles_imdb`
list for that movie, not just the catalog's primary title -- the current
`true_mismatch` bucket is very likely overcounting real mismatches by
close to half.

Built with `verify_mismatch_against_imdb_akas.py` (scratchpad, not yet
ported into this repo as a numbered script -- worth doing once/if a
title-match gate here starts consuming `alt_titles_imdb` directly rather
than just the single catalog title it uses today).
