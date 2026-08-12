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

## Gate 2 — `02_match_imdb.py`

**Input**: candidate rows. **Output**: alternate titles per id (doesn't
remove rows). This gate doesn't reject anything by itself — it produces the
evidence (`alt_titles_tmdb`, `alt_titles_imdb`) that later gates use to
decide whether a "different title on the poster" is actually wrong.

## Gate 3 — `03_bedrock_ocr.py`

**Input**: candidate rows. **Output**: `verdict` (`match` / `mismatch` /
`no_title_on_poster`) per id, from a vision-LLM reading the poster directly.
This is the single most important gate — it's the only one that actually
looks at the image content rather than metadata. Doesn't reject anything by
itself; feeds gate 8's logic and the manual mismatch review.

## Gate 4 — `04_comprehend_language.py`

**Input**: the text gate 3 read off each poster. **Output**: language code.
Informational only — language mismatch vs. `original_language` is expected
for ~79% of cases (international releases) and is never used alone to
reject a row.

## Gate 5 — `05_translate_titles.py`

**Input**: gate 4's language + gate 3's text. **Gate condition**: only
translates when `lang != en` AND `len(text) >= TRANSLATE_MIN_CHARS` AND
`overlap_before < TRANSLATE_BELOW` (see `utils/constants.py`). Re-scores
overlap after translation — this is what separates "foreign title, needs
translating to see it matches" from "genuinely doesn't match."

## Gate 6 — `06_dedupe_tmdb_metadata.py`

**Reject condition**: same `title` + `year` + `overview` (first 60 chars) as
another row, AND both ids independently confirmed live in TMDB (a
candidate pair with one dead id is not a real duplicate — see
[VALIDATION_LOGIC.md](VALIDATION_LOGIC.md)). Of a real pair, the weaker id
(by imdb_id → cast/crew → trailer → popularity, in that order) is excluded.

## Gate 7 — `07_dedupe_poster_md5.py`

**Reject condition**: byte-identical poster file (MD5 hash) shared with
another id. Independent of gate 6 — catches image reuse even when title/
year/overview don't match closely enough for gate 6 to group them. Keeps
the highest-`vote_count` id per MD5 group.

## Gate 8 — `08_collapse_compilations.py`

**Reject condition**: poster shared across 2+ ids (same signal as gate 7,
but here the ids are otherwise unrelated in title/year — not a metadata
duplicate, a shared *cover*) AND a distinct compilation/anthology entry for
that cover exists in TMDB. If no such entry exists, this gate does **not**
auto-resolve — it reports the group and leaves it for a human decision
(see `docs/RESULTS.md` for the two real cases we left un-collapsed).

## Gate 9 — `09_validate_corpus.py`

Not a gate itself — runs gates 1-8 in order and merges every rejection
(with its reason string) into `excluded_ids.csv`, everything else into
`validated_corpus.csv`.

## What's *not* a gate here

Poster/title mismatches that survive gates 2-5 unexplained are **not**
auto-excluded by any script — that decision (exclude vs. flag vs. manually
resolve with a found alt title) was made by hand for each of the 24 final
cases in the full run. See `docs/RESULTS.md` for why: several looked like
"wrong poster" but turned out to be a different, unrelated real film's
poster attached in error, which a script can detect but shouldn't silently
resolve.
