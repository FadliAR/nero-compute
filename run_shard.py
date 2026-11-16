#!/usr/bin/env python3
"""Generic shard runner for nero-compute.

Reads a job spec (JSON), takes the items whose index belongs to this shard
(strided: index % shards == shard, which balances heavy items), runs the
spec's task function from tasks.py on each item, and writes a structured
JSON result file.

Job spec format:
{
  "task":   "name of a function in tasks.py",
  "items":  [ {...}, ... ],          // one dict per work item
  "params": { ... },                 // optional, passed to every task call
  "env":    { "VAR": "value" }       // optional env vars set before running
}

Usage:
  python3 run_shard.py --spec jobs/example.json --shard 0 --shards 16 \
      --payload ./payload --out result.json
"""
import argparse
import importlib
import json
import os
import platform
import socket
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="path to job spec JSON")
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--shards", type=int, required=True)
    ap.add_argument("--payload", default="",
                    help="directory with read-only work material (may be empty)")
    ap.add_argument("--out", required=True, help="where to write result JSON")
    a = ap.parse_args()

    with open(a.spec) as f:
        spec = json.load(f)

    for k, v in spec.get("env", {}).items():
        os.environ.setdefault(k, str(v))

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    tasks = importlib.import_module("tasks")
    fn = getattr(tasks, spec["task"])

    items = spec["items"]
    mine = [(i, it) for i, it in enumerate(items) if i % a.shards == a.shard]
    ctx = {"payload": a.payload, "params": spec.get("params", {}), "cache": {}}

    results = []
    t_all = time.time()
    for idx, item in mine:
        t = time.time()
        r = fn(item, ctx)
        results.append({"index": idx, "item": item, "result": r,
                        "seconds": round(time.time() - t, 4)})
        print(f"  item {idx}: done in {results[-1]['seconds']}s", flush=True)

    out = {
        "task": spec["task"],
        "shard": a.shard,
        "shards": a.shards,
        "host": socket.gethostname(),
        "python": platform.python_version(),
        "items_total": len(items),
        "items_done": len(results),
        "compute_seconds": round(sum(r["seconds"] for r in results), 3),
        "wall_seconds": round(time.time() - t_all, 3),
        "results": results,
    }
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    print(f"shard {a.shard}/{a.shards}: {len(results)} items, "
          f"compute {out['compute_seconds']}s, wall {out['wall_seconds']}s")


if __name__ == "__main__":
    main()
