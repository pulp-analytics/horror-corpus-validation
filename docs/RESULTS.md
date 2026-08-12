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
kind of check ([07_dedupe_poster_md5.py](../scripts/07_dedupe_poster_md5.py))
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
