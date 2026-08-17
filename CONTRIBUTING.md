# Contributing

Thanks for your interest in contributing.

## How to contribute

1. Fork the repo and create your branch from `main`.
2. Make your changes, with a clear commit message.
3. Open a pull request describing what changed and why.
4. Link any related issue.

## Reporting issues

Use the Issues tab. Include steps to reproduce, expected vs. actual behavior,
and relevant environment details (OS, Python version, etc.) when applicable.

## Code style

Keep changes focused and readable. Match the existing style in the file
you're editing rather than introducing a new one.

## Validating claims about the real project

This repo ports methodology from a private pipeline that already ran, not
a spec someone wrote down. Two rules keep that honest:

1. Never cite the real project's mechanism from a docstring, a prior
   commit, or memory — verify it by reading the real script's source, or
   by matching real output data byte-for-byte. If you can't verify it,
   say so explicitly rather than presenting a guess as fact (see
   `docs/VALIDATION_LOGIC.md` for the pattern this repo uses: real
   intent, real mechanism if verified, this port's substitute if not).
2. When something in the real project can't be reproduced — e.g. it
   depended on that project's own internal run history — don't
   approximate it with a weak proxy just to have *something* that looks
   similar. Build the most robust version of the actual intent instead,
   using signals a fresh checkout can really compute, and label it as a
   deliberate improvement, not a port. (`utils/tmdb_completeness.py`'s
   cascade is the example: the real tiebreaker couldn't be reproduced,
   so gates 7/8 use the best real signal TMDB itself exposes instead of
   guessing at the original.)

## Don't ship untested robustness

Any defensive logic that isn't driven by an actual bug or an actual
real-data test case is a guess, and guesses in this codebase have
shipped backwards more than once. Real example: `10_collapse_compilations.py`
once excluded any TMDB search candidate whose id matched one of the
segment ids being resolved, reasoning that would prevent a "self-match."
Untested. The first time it was checked against real data, it excluded
the *correct* answer, because the canonical compilation entry
legitimately was one of the segment ids. It was removed, not kept as
"extra safety" — see that script's git history and
`docs/VALIDATION_LOGIC.md`.

Rule: if you're adding a guard, a fallback, or an exclusion for a case
you're imagining rather than one you've actually hit, either (a) find or
build a real test case that exercises it before merging, or (b) don't
add it yet.

## Fixtures are code

A committed sample CSV that silently drifts out of sync with what the
scripts reading it expect is as much a bug as bad logic. This repo has
two layers guarding against it: `tests/test_sample_data_freshness.py`
catches schema drift (column names), `tests/test_fixture_consistency.py`
catches value drift (e.g. a blank cell silently breaking an exact-match
join — this is the real bug that motivated adding it). If you touch a
fixture, run the test suite. If you touch a script's output shape,
either regenerate the fixture for real or rename it `*.stale` per
`test_sample_data_freshness.py`'s own instructions — don't leave a
fixture that no longer reflects reality uncommented.

## When two gates can disagree

Several gates in this pipeline (currently 7, 8, 9) can independently
reach a verdict on the same catalog id. If you add a new one, decide and
document its precedence against the existing ones in
`11_validate_corpus.py`'s `compute_dedup_exclusions()` and
`docs/VALIDATION_LOGIC.md` — don't let it silently win or lose based on
which order scripts happen to run in. That's exactly the bug fixed in
the commit that added `compute_dedup_exclusions()`: gate 9 ran before
gate 10 purely by code order, and on a real case that made it produce the
wrong answer.

## Docstrings describe stable behavior, not point-in-time findings

Code comments and docstrings should explain what the code does and why,
in a way that stays true regardless of when it's read. Evidence tied to
a specific date (a live API check, a real id's specific status as of
some date) belongs in `docs/RESULTS.md` or `docs/VALIDATION_LOGIC.md`,
dated, not frozen into a docstring where it can quietly go stale if the
real-world state it describes changes again.

## License

By contributing, you agree that your contributions will be licensed under
the project's MIT License.
