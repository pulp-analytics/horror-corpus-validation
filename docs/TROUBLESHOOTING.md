# Troubleshooting

**`TMDB_API_KEY not set`**
Copy `.env.example` to `.env`, fill in your key, and `export $(cat .env | xargs)`
before running — the scripts read from the environment, not the file
directly.

**`botocore.exceptions.AccessDeniedException` on Bedrock**
Model access for Nova Pro is granted separately from IAM permissions — check
Bedrock → Model access in the console for the region you're calling
(`us-east-1` by default). Approval isn't instant; it can take a few minutes
after you request it.

**`ModuleNotFoundError` for `boto3` / `requests` / `PIL`**
You're likely running with the system Python instead of the virtualenv —
confirm with `which python3`, then `pip install -r requirements.txt` in
that same environment. This is the single most common issue when switching
between shells/terminals.

**Nova Pro returns text that isn't valid JSON**
The prompt asks for JSON-only output but models occasionally wrap it in a
sentence or code fence. `03_bedrock_ocr.py` strips common
```` ```json ```` fences before parsing; if you still see failures on your
data, log the raw `result` before the `json.loads()` call to see what
came back.

**TMDB search (`08_collapse_compilations.py`) finds nothing for a known
compilation**
TMDB doesn't have every VHS/DVD compilation or TV anthology as its own
entry — this is expected for a meaningful minority of cases. See
[VALIDATION_LOGIC.md](VALIDATION_LOGIC.md#deciding-whether-a-shared-poster-is-a-compilation)
for how to handle that.

**Rate limiting / `429` from TMDB**
The scripts already sleep between calls, but if you're running several at
once against the same API key, space out `--delay` further or run scripts
sequentially (which `09_validate_corpus.py` already does).
