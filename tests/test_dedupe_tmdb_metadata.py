"""Pure-function tests for scripts/07_dedupe_tmdb_metadata.py's
completeness_key() -- the imdb_id -> credits -> trailer -> popularity
cascade tiebreaker. No network needed."""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "dedupe_tmdb_metadata", Path(__file__).resolve().parents[1] / "scripts" / "07_dedupe_tmdb_metadata.py")
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

completeness_key = mod.completeness_key


def cache_row(has_imdb_id=0, credits=0, has_trailer=0, popularity=0.0):
    return {"has_imdb_id": has_imdb_id, "credits": credits, "has_trailer": has_trailer, "popularity": popularity}


def test_imdb_id_presence_wins_over_everything_else():
    with_imdb = cache_row(has_imdb_id=1, credits=0, has_trailer=0, popularity=0.1)
    without_imdb = cache_row(has_imdb_id=0, credits=999, has_trailer=1, popularity=999.0)
    assert completeness_key(with_imdb) > completeness_key(without_imdb)


def test_credits_breaks_tie_when_imdb_id_equal():
    more_credits = cache_row(has_imdb_id=1, credits=50, has_trailer=0, popularity=0.0)
    fewer_credits = cache_row(has_imdb_id=1, credits=5, has_trailer=1, popularity=999.0)
    assert completeness_key(more_credits) > completeness_key(fewer_credits)


def test_trailer_breaks_tie_when_imdb_id_and_credits_equal():
    with_trailer = cache_row(has_imdb_id=1, credits=10, has_trailer=1, popularity=0.0)
    without_trailer = cache_row(has_imdb_id=1, credits=10, has_trailer=0, popularity=999.0)
    assert completeness_key(with_trailer) > completeness_key(without_trailer)


def test_popularity_is_last_resort_tiebreaker():
    more_popular = cache_row(has_imdb_id=1, credits=10, has_trailer=1, popularity=5.5)
    less_popular = cache_row(has_imdb_id=1, credits=10, has_trailer=1, popularity=1.2)
    assert completeness_key(more_popular) > completeness_key(less_popular)


def test_missing_fields_default_to_zero():
    assert completeness_key({}) == (0, 0, 0, 0.0)
