# Validation Logic

## Deciding whether a "mismatch" is real

A vision-LLM (Amazon Nova Pro) reviewing a poster against its catalog title
returns `mismatch` far more often than there's an actual problem with the
poster. Before excluding anything, run these checks **in this order** — each
one was added because a real false-positive slipped through the previous
ones:

1. **Catalog title itself.** Compare the visible text against the film's
   own `title` and `original_title` — sounds obvious, but a "mismatch"
   verdict from a vision model is sometimes a doubt about *genre* ("this
   doesn't look like a horror poster"), not the title. In our real run this
   single check resolved the largest chunk of remaining cases by far.
2. **Alternate titles** — IMDb AKAs and TMDB `alternative_titles`. A
   different-but-real title on the poster is very often a foreign release
   or reissue name, not an error.
3. **Multiple OCR engines**, not just one. Different OCR engines fail on
   different posters — cross-check at least two before concluding the text
   truly isn't there.
4. **Accent and whitespace normalization.** `"BlackOps"` vs `"Black Ops"`,
   or `"Isoäiti"` vs `"Isoaiti"` — these are the same text and should score
   as a match. Compare both the raw strings and accent-stripped/
   space-stripped variants; take the best score.

Only after all four checks fail to explain the poster should it be treated
as a genuine mismatch candidate.

## Deciding whether two ids are really duplicates

Same `title` + `year` + `overview` (first ~60 chars, case-insensitive) is
the candidate signal — but **verify both ids are still live in TMDB before
trusting it**. A meaningful fraction of candidates turn out to be one live
id and one that's since been deleted from TMDB (404) — not a real duplicate
at all, just a stale reference.

For confirmed duplicates (2+ ids still alive), the intent is simple: once
you're sure it's the same film, keep whichever entry has the richer
metadata. The real project's actual mechanism for that was whichever id
already had *this project's own metrics* computed on it — a signal from
that specific run's own processing history, not anything TMDB itself
exposes, so it can't be reproduced by a fresh checkout of this repo.
`07_dedupe_tmdb_metadata.py` approximates the same intent instead with
cast/crew count from `/movie/{id}/credits` — a real, computable proxy for
"more complete/curated entry," not a literal reproduction of the real
run's mechanism. (An earlier version of this doc also listed `imdb_id`
presence, trailer presence, and `popularity` as further tiebreakers in a
4-step cascade — no real script implementing that combination could be
found on a 2026-08-16 re-check; removed rather than left as an
unverified claim. If an earlier, different heuristic really was tried
and dropped, that's a real possibility worth remembering — just not one
to port forward as if it were the final logic.)

## Deciding whether a shared poster is a compilation

Multiple catalog ids sharing the exact same `poster_path` is a strong
signal they're segments of one VHS/DVD compilation or TV anthology. Check
whether the compilation/anthology **itself** has a TMDB entry (search by
the text visible on the shared poster) — if it does, collapse all the
segment ids into that one instead of picking one of them arbitrarily (the
compilation's own entry usually has its own correct poster too).

If no such entry exists, this is a judgment call, not something to
auto-resolve: leaving the segments un-collapsed loses nothing (no wrong
data), while collapsing into an arbitrarily-chosen segment misrepresents
the others.

*Provenance note (2026-08-16 re-check):* the real project's own
`data/excluded_compilation.csv` confirms the exact ids in every example
below (Sheets of Gore's 6, Ultimate Zombie Feast's 2, Late Night Horror's
4) — the outcomes are verified real, not invented. The script that
actually *produced* that file couldn't be found in the local copy of the
real project; the narrative doc that would name it
(`docs/HISTORIAL_PROYECTO.md`, "Fase 11") is on a currently-disconnected
external drive. `09_collapse_compilations.py`'s TMDB-search mechanism is
this port's reconstruction of an approach consistent with the real
outcomes, not a line-by-line verified reproduction — same caveat applies
to `08_dedupe_poster_md5.py`'s tiebreaker (see that script's docstring).

## Common false-positive patterns we found

- **Genre doubt, not title mismatch** — vision model second-guesses an
  obscure or non-mainstream title's fit for the genre even when the title
  text matches exactly.
- **Same-name unrelated films** — two real, different films sharing a
  generic title and release year coincidentally (verify director/cast
  before assuming duplicate).
- **Franchise/anthology numbering** — `"Vol. 2"`, `"Part 3"` etc. truncated
  out of a naive title-prefix match, making genuinely different episodes
  look like duplicates.
