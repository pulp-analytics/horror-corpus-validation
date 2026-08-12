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

AWS_REGION = "us-east-1"
