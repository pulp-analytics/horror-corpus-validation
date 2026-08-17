# Models

Companion to the sibling [poster-metrics-pipeline](https://github.com/pulp-analytics/poster-metrics-pipeline)'s
`docs/MODELS.md`, which pins CLIP/SigLIP/YuNet by hash or Hub revision.
This repo only has one model dependency, and it's a different kind of
problem: it can't be pinned the same way.

## Amazon Bedrock (`us.amazon.nova-pro-v1:0`) -- 06_bedrock_ocr.py

Not pinnable from the caller's side. `us.amazon.nova-pro-v1:0` looks like
a version (the `v1:0` suffix), but it names a managed, hosted model --
AWS can update what that id actually serves server-side, with no
changelog visible to callers and no way to request "the exact weights
that answered this call last month." This is structurally different from
the Hugging Face / open_clip pins in the sibling repo, where the caller
controls (and can freeze) exactly which artifact loads.

What's captured instead, as the closest available substitute for a real
pin:

- **`06_bedrock_ocr.py`'s output CSV records `model` per row** (the
  `--model` value used for that call, e.g. `us.amazon.nova-pro-v1:0` vs.
  `us.amazon.nova-lite-v1:0` -- see `fields` in that script). Necessary
  but not sufficient: two rows can carry the identical `model` string
  while AWS quietly served different underlying weights for each, and
  this column can't tell you that happened.
- If you need a tighter provenance signal than the model id string alone,
  the practical option is a **timestamp per row** (not currently written)
  as a rough proxy for "which AWS-side model revision was live" --
  something to add if a future re-run's numbers ever need explaining
  against an older run's.

## Not applicable

- **TMDB API**, **Amazon Comprehend**, **Amazon Translate**: managed
  API calls, not model artifacts this repo selects or loads -- there's no
  model_id knob exposed to pin in the first place.

## Building a human ground-truth set

Version pinning answers "did the artifact change." It says nothing about
whether the model's *answer* is actually correct -- for a managed model
like Nova that can't be pinned at all, that question matters more, not
less. `06_bedrock_ocr.py` had no ground-truth check of any kind before
this: no hand-labeled set to compare its match/mismatch/no_title_on_poster
verdict against, unlike the sibling repo's `--validate` sanity checks for
CLIP/SigLIP.

`data/ground_truth/bedrock_ocr_sample.csv` is a first one: 100 real
posters (id, catalog title, poster_path only -- deliberately no verdict
column, so review stays blind to what Nova said), stratified by drawing
from the real full corpus's actual `title_ocr_qa_verdict` distribution
(106,832 accurate / 17,670 inaccurate / 4,869 no_title_on_poster at
sampling time) rather than uniformly at random:

| stratum | pool size | sampled |
|---|---|---|
| accurate | 106,832 | 40 |
| inaccurate | 17,670 | 40 |
| no_title_on_poster | 4,869 | 20 |

Uniform random sampling would have made ~74% of the 100 "accurate" --
mostly the easy case, wasting review budget on what's already known to
work. Oversampling `inaccurate` and `no_title_on_poster` spends the
review where the failure modes actually are: false accepts (Nova says
match, a human disagrees) and false rejects (Nova says mismatch on what's
actually a legitimate foreign/reissue title -- the exact failure this
script's own docstring already calls out). Drawn with a fixed seed
(`random.seed(20260814)`, the date this sample was drawn) so it's a
documented, non-arbitrary 100, not "some posters."

**Review workflow:**
```bash
python3 scripts/qa/build_bedrock_ocr_review_page.py
open data/ground_truth/bedrock_ocr_review.html   # local file, not published anywhere
```
The page shows each poster and its catalog title, nothing else, and asks
for a blind match/mismatch/no_title_on_poster/**unjudgeable** call
(4 options, not 3 -- see below). Poster images are embedded as base64
data URIs at build time rather than loaded live from TMDB, since a
`file://` page making live cross-origin image requests gets silently
blocked by several browsers' tracking-protection features. Progress
autosaves to the browser's localStorage; an "Import progress" button
re-loads a previously-exported CSV so a partial review session can
resume without redoing already-answered posters; an Export CSV button
writes `bedrock_ocr_ground_truth_human_labels.csv` (id, title,
original_title, original_language, poster_path, stratum, human_verdict,
human_note) once done.

**Why a 4th verdict, "unjudgeable":** 48 of the 100 sampled posters are
non-English by TMDB's own `original_language` field. A reviewer who
doesn't read that poster's script (Japanese, Cyrillic, Thai, ...) has no
way to judge match/mismatch from the poster alone. The page shows
`original_title` alongside the catalog `title` whenever they differ --
from TMDB's own catalog metadata, not derived from Nova's OCR read, so
it doesn't anchor the reviewer to the model's own possibly-wrong answer.
For posters where even that isn't enough, "unjudgeable" keeps those rows
out of accuracy/precision/recall entirely rather than coercing a guess
that would add noise to the ground truth.

**Status: complete.** All 100 posters reviewed: 52 match, 7 mismatch,
16 no_title_on_poster, 25 unjudgeable (excluded from scoring below).

**Preliminary signal** (comparing the human labels against `stratum` --
i.e. against whatever Nova said *at the original full-corpus run*, not a
fresh call -- so this is a sanity check on the historical numbers, not a
validation of today's model):

| stratum | judged (non-unjudgeable) | agreed with Nova's original verdict | rate |
|---|---|---|---|
| accurate | 36 | 33 | 91.7% |
| inaccurate | 22 | 4 | 18.2% |
| no_title_on_poster | 17 | 16 | 94.1% |

Naive agreement across all 75 judged rows is 70.7%, but that oversamples
the hard `inaccurate` bucket on purpose (see the sampling table above) --
weighting each stratum's agreement rate by its real share of the full
corpus (82.6% / 13.7% / 3.8%) gives a corpus-representative estimate of
**81.7%**. The more interesting number is inside `inaccurate` alone:
only 18.2% of Nova's original "mismatch" calls held up under human
review, and the errors skew almost entirely one direction -- 19 of 22
disagreements are Nova saying mismatch on what a human confirmed was
actually a match (a false reject), versus only 3 false accepts. Nova's
`inaccurate` verdict, historically, was wrong about 4 times out of 5.

**`--validate` mode, run live (2026-08-15):**
```bash
export AWS_PROFILE=your-bedrock-profile
python3 06_bedrock_ocr.py --validate
```
Runs `--model` live against all 75 judged ground-truth rows (skipping
`unjudgeable` ones -- they never reach Bedrock at all), writes the raw
per-row comparison to `data/ground_truth/bedrock_ocr_validate_results.csv`,
and prints overall accuracy plus per-class precision/recall/support and a
confusion matrix. This is the rigorous version of the preliminary signal
above: it scores whatever Nova actually serves *today*, not the
historical `stratum` snapshot -- meaningfully different given Bedrock
can't be pinned (see this doc's top section). Pure-function unit tests
for the accuracy/precision/recall math live in `tests/test_bedrock_ocr.py`
and don't need AWS credentials; only actually running `--validate` does.

**Real result: 93.3% (70/75), notably higher than the 81.7% preliminary
estimate.** This is exactly the non-determinism this doc's top section
warns about, made concrete: `us.amazon.nova-pro-v1:0` today is not the
same answer machine that built this corpus.

| class | precision | recall | support |
|---|---|---|---|
| match | 96.1% | 94.2% | 52 |
| mismatch | 62.5% | 71.4% | 7 |
| no_title_on_poster | 100.0% | 100.0% | 16 |

The finding that drove this whole investigation -- "Nova's `mismatch`
verdict was wrong about 4 times out of 5" -- does not hold up against a
live call. Today's model gets `mismatch` right 71.4% of the time (5 of
7), a large jump from the 18.2% the historical `stratum` comparison
found. Confusion matrix (rows=human, cols=live): of the 52 true
`match` rows, 49 matched live and 3 came back `mismatch`; of the 7 true
`mismatch` rows, 5 matched live and 2 came back `match`. Small support
on `mismatch` (n=7) means don't over-read the exact 62.5%/71.4% -- but
the direction and the size of the jump are the real finding: this gate
is more trustworthy today than the numbers it was built on suggested,
and the only way to know that was to actually run it, not reason about
whether Bedrock output should be stable.
