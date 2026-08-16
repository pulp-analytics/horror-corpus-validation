"""Shared thresholds and paths used across the validation pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

TMDB_HORROR_GENRE_ID = 27

# poster_title_match.py thresholds (see docs/VALIDATION_LOGIC.md)
TRANSLATE_BELOW = 0.35
TRANSLATE_MIN_CHARS = 60
SUSPECT_BELOW = 0.35

# vision drift-review: below this alt-title overlap score, a Nova/Claude
# "mismatch" verdict is treated as unexplained (see docs/VALIDATION_LOGIC.md)
ALT_TITLE_OVERLAP_THRESHOLD = 0.5

# 09_collapse_compilations.py: minimum overlap/fuzzy title-match score to
# accept a TMDB search result as the real compilation/anthology entry
# (see docs/VALIDATION_LOGIC.md, "Deciding whether a shared poster is a
# compilation")
COMPILATION_MATCH_THRESHOLD = 0.55

# Default AWS region -- only used if AWS_DEFAULT_REGION isn't set in the
# environment/.env (see utils/aws_config.get_client()).
AWS_REGION = "us-east-1"
