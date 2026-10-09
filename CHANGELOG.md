# Changelog

## 1.1.1 (2026-10-09)

- Demo reset no longer races review writes: it holds a self-expiring lease (`demo_control/review_lock`); opening or closing a review writes to that document inside its transaction and gets 409 `reset_in_progress` while the lease is live. The reset ends by sweeping flags left without a case. Before, a review opened mid-reset left the group blocked with no case (43 companies on the small dataset). Regression: `tests/live_reset_race.py` and `ResetLeaseTests`.
- Concentration panel: the semantic example uses the seed's real activity descriptions and no longer claims the three share no word (two share "obras").
- Per-run test outputs (`live-graph-adversarial`, `http-adversarial`, `browser-large-graph`, `stress-results`) are no longer versioned.

## 1.1.0 (2026-10-06)

- UI: layout MongoDB 2026 "Dark Stage v4" (tokens mais escuros, Special Gothic / Source Code Pro locais, motivos de escada e grade, movimento escalonado).
- Alert stream is now asynchronous with a 64-connection cap: 45 open streams no longer starve the threadpool (`/health/live` went from 12 s to 5 ms).
- `GRAPH_MAX_TIME_MS` is enforced with `pymongo.timeout()`; the driver ignored per-operation `maxTimeMS` under client `timeoutMS`. Exhausted deadlines are not retried.
- Request id lists are bounded per item and refuse unknown fields; driver `DocumentTooLarge` returns 413 instead of 500. Error bodies no longer include the cluster host.
- `scripts/reset_demo.py`: one idempotent command for data, indexes, review state, embeddings and Search/Vector indexes, with a guard that refuses non-`_test` databases without `ALLOW_DEMO_DB_WRITE=1`. `run_all.sh` delegates to it (it previously generated 20 groups because `GROUPS` is a read-only bash variable).
- Dependencies: pymongo 4.18.2, python-dotenv 1.2.2 (pip-audit findings).
- New adversarial suites: offline inputs, hostile graph topologies in a throwaway database, HTTP probes against the running API and a 1,200-node browser render.

## 1.0.0 (2026-09-30)

First public release.

- Repository rebuilt with a clean, single-commit history.
- English README and repository description, with screenshots captured against a real Atlas cluster.
- MIT license.
- Internal notes, presentation decks, test-output snapshots, and tooling configuration removed from the repository.
