# nero-compute

A free parallel batch computer built on GitHub Actions.

Any job that splits into independent items (a list of inputs, one result
per input) can run here: write a task function once, describe the work as
a JSON spec, and the `batch` workflow fans it out across up to ~20
parallel runner jobs and merges the results.

## How it works

- `run_shard.py` — generic runner. Given a spec and a shard index it
  processes items `index % shards == shard` (strided, so heavy items
  spread evenly) and writes a structured JSON result.
- `tasks.py` — task implementations. A task is `fn(item, ctx) -> result`.
  Add new job types here; the workflow never changes.
- `jobs/*.json` — job specs: `{"task": ..., "items": [...],
  "params": {...}, "env": {...}}`.
- `.github/workflows/batch.yml` — `workflow_dispatch` with inputs
  `shards`, `spec`, `payload_repo`. A setup job builds the matrix,
  shard jobs run in parallel and upload `result-<shard>` artifacts,
  a collect job merges them into `merged-result`.
- `merge_results.py` — merges shard results, checks for missing or
  duplicated item indices, totals numeric results.

Payload: if the work needs read-only material from another public repo
(e.g. an engine file), name it in `payload_repo`; each shard clones it
into `./payload`. Nothing secret ever goes in this repo — workflows use
only the built-in `GITHUB_TOKEN`.

## Run a batch

```sh
gh workflow run batch.yml --repo FadliAR/nero-compute \
  -f shards=16 -f spec=jobs/perft-proof.json \
  -f payload_repo=https://github.com/FadliAR/tiny-chess-01
gh run watch --repo FadliAR/nero-compute
gh run download <run-id> --repo FadliAR/nero-compute --dir results
python3 merge_results.py --dir results --out merged.json
```

Or from the GitHub web UI: Actions → batch → Run workflow.

## Included jobs

- `jobs/perft-proof.json` — chess perft (exact legal-move tree counts)
  over 20 positions, using the engine from the payload repo. Fully
  deterministic: every shard's numbers must match a local run exactly.
- `jobs/demo-sha256.json` — trivial SHA-256 chain demo.

## Free-tier notes

- Public repo: standard GitHub-hosted runners are free with generous
  fair-use minutes; concurrency for the Free plan is ~20 jobs at once,
  so keep `shards` at 20 or below.
- Private repos get 2,000 runner minutes/month on the Free plan.
- No paid features are used anywhere in this repo.
