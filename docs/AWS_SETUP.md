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
sample run costs well under $1. Nova Pro vision calls are the largest
single cost per call; Comprehend/Translate are negligible at this scale.
There is no infrastructure to shut down after a run (no EC2, no
provisioned throughput) — see `horror-analysis-infrastructure` for the
separate repo covering larger batch/EC2 runs and cost-safety tooling.

## Optional: IMDb dataset for `03_match_imdb.py`

The IMDb non-commercial datasets (not an AWS resource) add a second,
independent source of alternate titles beyond TMDB's own
`alternative_titles`. Download `title.akas.tsv.gz` from
[datasets.imdbws.com](https://datasets.imdbws.com/) if you want that extra
cross-check — the script works fine without it, using TMDB's API alone.
