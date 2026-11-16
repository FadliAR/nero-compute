#!/usr/bin/env python3
"""Merge per-shard result files into one summary.

Usage:
  python3 merge_results.py --dir results --out merged.json

--dir holds either result.json files directly or artifact subdirectories
(result-<shard>/result.json), as produced by actions/download-artifact.
Prints a human summary and writes the merged JSON. Exits non-zero if any
item index is missing or duplicated.
"""
import argparse
import glob
import json
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.dir, "**", "*.json"), recursive=True))
    files = [f for f in files if os.path.basename(f) != os.path.basename(a.out)]
    if not files:
        print("no result files found", file=sys.stderr)
        sys.exit(1)

    merged = {}
    shards = []
    total_compute = 0.0
    for f in files:
        with open(f) as fh:
            d = json.load(fh)
        shards.append({"file": f, "shard": d.get("shard"),
                       "items_done": d.get("items_done"),
                       "compute_seconds": d.get("compute_seconds"),
                       "wall_seconds": d.get("wall_seconds")})
        total_compute += d.get("compute_seconds", 0.0)
        for r in d["results"]:
            i = r["index"]
            if i in merged:
                print(f"DUPLICATE index {i}", file=sys.stderr)
                sys.exit(1)
            merged[i] = r

    idx = sorted(merged)
    missing = [i for i in range(idx[-1] + 1) if i not in merged] if idx else []
    numeric_total = sum(r["result"] for r in merged.values()
                        if isinstance(r["result"], (int, float)))
    summary = {
        "items": len(idx),
        "indices": idx,
        "missing": missing,
        "numeric_result_total": numeric_total,
        "total_compute_seconds": round(total_compute, 3),
        "shards": shards,
        "results": [merged[i] for i in idx],
    }
    with open(a.out, "w") as f:
        json.dump(summary, f, indent=1)

    print(f"items merged: {len(idx)} from {len(files)} shard files")
    print(f"missing indices: {missing if missing else 'none'}")
    print(f"numeric result total: {numeric_total}")
    print(f"total compute (sum over shards): {summary['total_compute_seconds']}s")
    if missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
