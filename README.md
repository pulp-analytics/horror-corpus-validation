# poster-corpus-validation

Data quality and validation tooling for a large-scale movie poster corpus
(built and run against 69,789 horror titles sourced from TMDB, part of a
145k-title multi-genre project). Catches three real problems that don't show
up in aggregate stats: **duplicate catalog entries**, **compilation/anthology
posters misattributed to individual films**, and **poster/title mismatches**
— using vision-LLM cross-checks (Amazon Nova Pro) against OCR and
alternate-title databases (IMDb, TMDB) to tell real errors apart from false
positives.

Part of the [Pulp Analytics](https://github.com/pulp-analytics) horror
poster analysis project ("The Anatomy of Fear"). Real results from the full
run: [docs/RESULTS.md](docs/RESULTS.md).

## Quick start

```bash
git clone https://github.com/pulp-analytics/poster-corpus-validation
cd poster-corpus-validation
make setup
source .venv/bin/activate
cp .env.example .env   # fill in TMDB_API_KEY and AWS_PROFILE
make test              # pure-function unit tests, no API calls
make validate-sample   # runs the full pipeline on 100 sample TMDB ids
```

`make validate-sample` writes three files to `data/sample_output/`:
- `validated_corpus.csv` — the clean rows
- `excluded_ids.csv` — everything removed, with a reason per row
- `qa_report.json` — summary counts

## Why this exists

A wrong poster still produces *a* color palette, *a* face detection result,
*a* CLIP embedding — the pipeline runs fine and the corpus looks complete.
The problem only shows up when you check what's actually in the image
against what the catalog says it is. See
[docs/PHASE_1_OVERVIEW.md](docs/PHASE_1_OVERVIEW.md) for the full rationale
[docs/VALIDATION_LOGIC.md](docs/VALIDATION_LOGIC.md) for the exact decision
rules (in what order to check things, and why), and
[docs/VALIDATION_GATES.md](docs/VALIDATION_GATES.md) for what each script
rejects and why.

## Validation methodology

Every non-trivial gate in this repo is checked the same three-layer way
before it's trusted, not just built and assumed correct:

1. **Deterministic pre-filter first, where one exists** — free, exact
   signals (OCR text presence, MD5 hash, TMDB metadata match) run before
   any model call, both to cut cost and because they're the least
   ambiguous signal available (e.g. gate 4's poster-type check: Rekognition
   `DetectText` decides the reachable-text cases for free, Nova is only
   called for the zero-OCR remainder).
2. **Vision-LLM cross-check (Amazon Nova Pro)** — for what the deterministic
   layer can't decide alone: does the image actually match the catalog
   title, is this really poster art, does it cross a moderation threshold.
3. **Blind human ground truth** — every LLM/deterministic signal reported
   in [docs/RESULTS.md](docs/RESULTS.md) is scored against a real human
   verdict before being trusted, collected with the self-contained review
   pages in `scripts/qa/` and recorded in `data/ground_truth/` (2,500+
   labeled rows across poster-type, OCR-mismatch, and moderation alone).
   Reviews are blind by construction — the page shows the poster and a
   plain question, never the score or system that produced it, to avoid
   anchoring the reviewer.

This is what caught real problems a single layer would have missed — e.g.
an isolated Nova prompt beating a combined mega-prompt by 3-6x on the same
human-reviewed set (see RESULTS.md, "Isolated prompts vs. one combined
mega-prompt"). Not every gate needs all three layers (IMDb's `isAdult` flag
is itself curated ground truth, so gate 2 skips the human-review leg
entirely — see RESULTS.md for why), but every accuracy number cited
anywhere in this repo's docs traces back to a real, blind human review, not
an assumption.

## Structure

```
scripts/
  01_tmdb_enumerate.py       Pull candidates from TMDB (--genre, default 27=Horror)          [TMDB only]
  02_filter_isadult.py       Drop real adult-content titles, earliest possible cut           [TMDB only]
  03_verify_poster_exists.py Does this row have a reachable poster at all?                   [TMDB only]
  04_filter_poster_type.py   Is this actually poster art, not a still/stock photo/blank?      [needs AWS]
  05_fetch_alt_titles.py     Alternate titles: TMDB API + optional IMDb AKAs                 [TMDB only]
  06_bedrock_ocr.py          Vision-LLM (Nova Pro) reads the poster directly                 [needs AWS]
  07_comprehend_language.py  Language of the poster's visible text                           [needs AWS]
  08_translate_titles.py     Re-score non-English text after translation                     [needs AWS]
  09_dedupe_tmdb_metadata.py Same title+year+overview, different id?                         [TMDB only]
  10_dedupe_poster_md5.py    Same exact poster image file used twice?                        [TMDB only]
  11_collapse_compilations.py Same poster shared across multiple ids?                        [TMDB only]
  12_validate_corpus.py      Orchestrates 1-9, writes final outputs                    [needs AWS, 04-06]
  13_find_alternate_posters.py Discover + download TMDB poster variants                      [TMDB only]
  14_score_alternate_posters.py Score variants vs. catalog title, propose swaps —
                                see docs/RESULTS.md "Gate 8-9" for an important
                                caveat: uses Bedrock, not the real project's
                                Rekognition-based version. --engine rekognition        [needs AWS]
                                is also available and needs no Bedrock.
  15_content_moderation.py   Gore/violence/sexual-content flag, Nova + Rekognition cross-check [needs AWS]
  prep_adult_tconsts.py      One-time: extract isAdult=1 tconsts from IMDb title.basics.tsv   [local]
  utils/                     Shared AWS clients, constants, text matching
  qa/                        Not pipeline stages -- tooling that produced this repo's own
                              ground truth: build_poster_type_review_page.py,
                              build_bedrock_ocr_review_page.py,
                              build_mega_prompt_review_page.py (self-contained HTML pages
                              for blind human review), nova_mega_prompt_comparison.py,
                              check_live_drift.py (live-checks docs' cited real examples
                              against TMDB today). See "Validation methodology" above.

  [TMDB only] steps need just a TMDB_API_KEY -- runnable anytime, no AWS
  account/sandbox required. [needs AWS] steps call Bedrock/Comprehend/
  Translate/Rekognition and need real AWS credentials (see docs/AWS_SETUP.md).
  12_validate_corpus.py's own gates 7-9 sub-steps are TMDB-only; it needs
  AWS only because it also runs 04-06 in its default (non --assemble-only) mode.

data/
  sample_input/sample_100_ids.csv   Real 100-id sample, runnable as-is
  sample_output/                    Written by the scripts
  decision_matrix.csv               Real exclusion decisions from the full run
  ground_truth/                     Real blind human-review labels this repo's own accuracy
                                     numbers are scored against (2,500+ rows) -- see
                                     "Validation methodology" above

docs/         Full writeups — overview, decision logic, AWS setup, results,
              models (docs/MODELS.md — why Bedrock can't be pinned the way
              the sibling repo pins CLIP/SigLIP/YuNet)
tests/        Unit tests for the pure matching logic (no network calls)
```

## Requirements

- Python 3.10+
- A [TMDB API key](https://www.themoviedb.org/settings/api) (free)
- AWS account with Bedrock (Nova Pro), Comprehend, and Translate access —
  see [docs/AWS_SETUP.md](docs/AWS_SETUP.md)

## License

MIT — see [LICENSE](LICENSE). Contributions welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md).
