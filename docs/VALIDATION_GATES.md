# Validation Gates

Each script in `scripts/` is a gate: a row either passes through unchanged,
gets flagged for review, or gets excluded with a logged reason. This is the
per-script breakdown — for the *cross-cutting* decision rules that span
multiple gates (e.g. "in what order do you check a mismatch"), see
[VALIDATION_LOGIC.md](VALIDATION_LOGIC.md).

## Gate 1 — `01_tmdb_enumerate.py`

**Input**: nothing (queries TMDB directly). **Output**: raw candidate rows.
No filtering here — this is corpus acquisition, not validation. Everything
downstream treats this list as "unverified."

## Gate 3 — `03_verify_poster_exists.py`

**Reject condition**: `poster_path` is empty, or the poster URL returns
anything other than 200. **The single largest rejection category in the
full run — ~90% of everything excluded (330 of 367 rows).** Any "analysis"
run on a row with no reachable poster is unreproducible: there's no way to
know what image, if any, a prior process actually looked at. Easy to
overlook because it's the least interesting-sounding check — worth running
first, before spending any vision-LLM budget.

## Gate 5 — `05_fetch_alt_titles.py`

**Input**: candidate rows. **Output**: alternate titles per id (doesn't
remove rows). This gate doesn't reject anything by itself — it produces the
evidence (`alt_titles_tmdb`, `alt_titles_imdb`) that later gates use to
decide whether a "different title on the poster" is actually wrong.

## Gate 6 — `06_bedrock_ocr.py`

**Input**: candidate rows. **Output**: `verdict` (`match` / `mismatch` /
`no_title_on_poster`) per id, from a vision-LLM reading the poster directly.
This is the single most important content gate — it's the only one that
actually looks at the image itself rather than metadata. Doesn't reject
anything by itself; feeds gate 11's logic and the manual mismatch review.

## Gate 7 — `07_comprehend_language.py`

**Input**: the text gate 6 read off each poster. **Output**: language code.
Informational only — language mismatch vs. `original_language` is expected
for ~79% of cases (international releases) and is never used alone to
reject a row.

## Gate 8 — `08_translate_titles.py`

**Input**: gate 7's language + gate 6's text. **Gate condition**: only
translates when `lang != en` AND `len(text) >= TRANSLATE_MIN_CHARS` AND
`overlap_before < TRANSLATE_BELOW` (see `utils/constants.py`). Re-scores
overlap after translation — this is what separates "foreign title, needs
translating to see it matches" from "genuinely doesn't match."

## Gate 9 — `09_dedupe_tmdb_metadata.py`

**Reject condition**: same `title` + `year` + `overview` (first 60 chars) as
another row, AND both ids independently confirmed live in TMDB (a
candidate pair with one dead id is not a real duplicate — see
[VALIDATION_LOGIC.md](VALIDATION_LOGIC.md)). Of a real pair, the weaker id
(by imdb_id → cast/crew → trailer → popularity, in that order) is excluded.

## Gate 10 — `10_dedupe_poster_md5.py`

**Reject condition**: byte-identical poster file (MD5 hash) shared with
another id. Independent of gate 9 — catches image reuse even when title/
year/overview don't match closely enough for gate 9 to group them. Keeps
the highest-`vote_count` id per MD5 group.

## Gate 11 — `11_collapse_compilations.py`

**Reject condition**: poster shared across 2+ ids (same signal as gate 10,
but here the ids are otherwise unrelated in title/year — not a metadata
duplicate, a shared *cover*) AND a distinct compilation/anthology entry for
that cover exists in TMDB. If no such entry exists, this gate does **not**
auto-resolve — it reports the group and leaves it for a human decision
(see `docs/RESULTS.md` for the two real cases we left un-collapsed).

## Gate 16 — `16_verify_celebrities.py`

**Flag condition** (doesn't hard-exclude, matches gate 15's `flagged`
pattern): Rekognition's `RecognizeCelebrities` finds a real, identifiable
person on the poster who isn't in that film's TMDB cast/crew, AND a Nova
plausibility check judges the mismatch `clearly_wrong` (not `plausible` or
`uncertain`) — see the script's own docstring for why only that verdict
flags. A celebrity match that fails the cast cross-check is a *candidate*
signal the poster art was recycled from an unrelated photo, not proof by
itself; the plausibility layer exists because the real project's own run
found a cast mismatch alone is right well under half the time. Not yet
live-verified on this repo's own corpus — see the script's docstring.
`scripts/qa/build_celebrity_review_page.py` builds the blind human-review
page for that once AWS credentials are available. See
`docs/RESULTS.md`, "Gate 16: celebrity verification," for the real
numbers this design is based on (across all four genres, with real
examples) and exactly what's still pending a live run.

## `12_validate_corpus.py`

Not a gate itself — runs gates 1-9 in order and merges every rejection
(with its reason string) into `excluded_ids.csv`, everything else into
`validated_corpus.csv`.

## What's *not* a gate here

Poster/title mismatches that survive gates 3-6 unexplained are **not**
auto-excluded by any script — that decision (exclude vs. flag vs. manually
resolve with a found alt title) was made by hand for each of the 24 final
cases in the full run. See `docs/RESULTS.md` for why: several looked like
"wrong poster" but turned out to be a different, unrelated real film's
poster attached in error, which a script can detect but shouldn't silently
resolve.
