#!/usr/bin/env python3
"""One-time prep: extract isAdult=1 tconsts from IMDb's title.basics.tsv.gz
into a small standalone list, so 02_filter_isadult.py's --adult-tconsts
doesn't need every shard to decompress and scan the full ~200MB/12.7M-row
original file to answer a question with a ~400k-row (3.3% of rows) answer.

Re-run this whenever you refresh the IMDb dataset -- it's a static
snapshot, not something the validation pipeline needs to regenerate
per-run.

  python3 prep_adult_tconsts.py --basics /path/to/title.basics.tsv.gz \
      --out data/adult_tconsts_isadult1.csv.gz
"""
from __future__ import annotations

import argparse
import gzip
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils.logging_setup import get_logger

log = get_logger("prep_adult_tconsts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--basics", type=Path, required=True, help="path to IMDb title.basics.tsv.gz")
    ap.add_argument("--out", type=Path, default=Path("data/adult_tconsts_isadult1.csv.gz"),
                     help="output path -- .gz suffix writes a gzipped list")
    args = ap.parse_args()

    t0 = time.time()
    n_total = n_adult = 0
    adult_tconsts: list[str] = []
    with gzip.open(args.basics, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline().rstrip("\n").split("\t")
        idx = {h: i for i, h in enumerate(header)}
        for line in f:
            n_total += 1
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= idx["isAdult"]:
                continue
            if parts[idx["isAdult"]] == "1":
                n_adult += 1
                adult_tconsts.append(parts[idx["tconst"]])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if str(args.out).endswith(".gz") else open
    with opener(args.out, "wt", encoding="utf-8") as f:
        f.write("\n".join(adult_tconsts) + "\n")

    log.info(f"scanned {n_total:,} rows in {time.time() - t0:.1f}s")
    log.info(f"wrote {args.out}: {n_adult:,} isAdult=1 tconsts ({n_adult / n_total * 100:.2f}% of rows)")


if __name__ == "__main__":
    main()
