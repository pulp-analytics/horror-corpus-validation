# AWS Setup

## Services used

- **Amazon Bedrock** (Nova Pro model) — vision QA of poster title text
- **Amazon Comprehend** — language detection
- **Amazon Translate** — translating non-English poster text before
  re-scoring against the catalog title

## One-time setup

1. Request Bedrock model access for `amazon.nova-pro-v1:0` (or the
   `us.amazon.nova-pro-v1:0` cross-region inference profile) in the AWS
   Console → Bedrock → Model access. This is a one-time approval step per
   account/region and can take a few minutes.
2. Create an IAM user or role with:
   - `bedrock:InvokeModel` (scoped to the Nova Pro model ARN if you want it
     tight)
   - `comprehend:DetectDominantLanguage`
   - `translate:TranslateText`
3. Configure a named profile locally:
   ```bash
   aws configure --profile horror-validation
   ```
4. Copy `.env.example` to `.env` and set `AWS_PROFILE=horror-validation`
   (and `TMDB_API_KEY`).

## Cost note

These are all pay-per-request services with no idle cost — a 100-row
sample run (on your own laptop, no AWS compute needed) costs well under $1.
Nova Pro vision calls are the largest single cost per call; Comprehend/
Translate are negligible at this scale. `04_bedrock_ocr.py --model` accepts
any Bedrock model id that supports image input, so swapping in Nova Lite
(~18x cheaper, less precise) for a large run is a one-flag change, not a
code change. See "Running at scale" below for
what a full-corpus run actually needs.

## Running at scale

The 100-id sample runs fine on a laptop. For a full corpus (tens of
thousands of titles), the bottleneck is API call volume, not compute — none
of these steps need a GPU or heavy CPU. What actually ran the full
69,789-title horror corpus for this project:

**Services**: TMDB API (candidate enumeration, poster verification,
alternate titles), Amazon Bedrock — Nova Pro (vision title check), Amazon
Comprehend (language detection), Amazon Translate, Amazon EC2 (just to host
the long-running script — no GPU instance type needed).

**Pattern**: a single small EC2 instance (`t3.small` / `c5.large` is
plenty) running the scripts under `nohup`/`screen` so a dropped SSH
connection doesn't kill an hours-long run. For a corpus large enough that
sequential API calls become the bottleneck, shard by id range across a
handful of small instances in parallel (same shape as the sharded EC2 jobs
in the sibling `horror-metrics-pipeline` repo — the sharding there is for
GPU throughput; here it's purely to parallelize API calls within TMDB/
Bedrock rate limits). Either way, `instance-initiated-shutdown-behavior
terminate` plus a `shutdown -h now` at the end of the script means each
instance bills only for its own runtime and cleans itself up.

Cost-safety tooling for running this unattended (budget alerts, anomaly
detection, a pre-flight account check) lives in the sibling
`horror-analysis-infrastructure` repo, not here — that repo exists
specifically because it's easy to point a batch job at the wrong AWS
account by accident.

## Optional: IMDb dataset for `03_fetch_alt_titles.py`

The IMDb non-commercial datasets (not an AWS resource) add a second,
independent source of alternate titles beyond TMDB's own
`alternative_titles`. Download `title.akas.tsv.gz` from
[datasets.imdbws.com](https://datasets.imdbws.com/) if you want that extra
cross-check — the script works fine without it, using TMDB's API alone.

The IMDb file is keyed by IMDb's own id (a `tt...` tconst), not TMDB's, so
the script needs one to join on. It fetches this automatically from TMDB's
`external_ids` endpoint the first time you pass `--akas` (one extra API
call per id, cached in the output JSON so it isn't re-fetched on later
runs) — you don't need to supply it yourself unless your input CSV already
happens to have an `imdb_id` column, in which case that's used instead.
