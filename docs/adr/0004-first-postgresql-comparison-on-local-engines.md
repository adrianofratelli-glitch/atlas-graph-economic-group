# ADR 0004 — First PostgreSQL comparison: local engines, same hardware, correctness before latency

**Status:** accepted · **Date:** 2026-11-12

## Context

[`docs/comparison-protocol.md`](../comparison-protocol.md) defined a MongoDB ×
PostgreSQL `WITH RECURSIVE` comparison and was deferred for lack of an equivalent
environment. Atlas reached over the internet against a PostgreSQL on a laptop is
explicitly not a valid performance comparison under that protocol.

A laptop can, however, run **both engines locally on the same hardware**, with no
network between client and server. That satisfies the protocol's "same resources,
same network path" condition for a first, reduced round.

## Decision

`queries/compare_postgres.py` runs that round:

- `mongod` 8.2 and PostgreSQL 16 (embedded through `pgserver`) on the same
  machine, 1 GB of cache each (`--wiredTigerCacheSizeGB 1`, `shared_buffers = 1GB`),
  other settings at their defaults. The local `mongod` is 8.2.7 Community; the demo
  runs on Atlas 9.0 (M20, shared). Same pipeline, different server version and
  host, so these numbers do not describe the demo cluster;
- the same fixtures in both engines: the evidence-page curve (1, 2, 4, 6 levels × 1–3
  children), the 1,200-child hub, and hostile shapes (cycle, cross-holding with
  individual partners, depth shorter than the chain, more roots than the cap —
  once with a shared child only and once with a distinct descendant per root, so a
  different choice of the five roots would change the answer —
  an edge to a controller missing from the registry), over 100,000 random
  background companies so indexes are not serving only the fixtures;
- MongoDB executes the versioned pipeline (`ownership._pipeline_grupo`);
  PostgreSQL executes one recursive query applying the same root, reach and
  exposure rules. `UNION` over (edge, level) terminates on cycles and bounds the
  rows to edges × (depth + 1), like the breadth-first visit of `$graphLookup`;
- a cell only receives timings after both engines return the **same companies,
  edges, cents and reach reasons**, also checked against the product function
  `economic_group`;
- timings are end to end in the Python client, one query plus the same minimal
  post-processing on both sides, engine order alternated, 100 samples
  per engine and cell at 1 and 8 concurrent clients. Each worker thread warms both
  engines 10 times before measuring, which opens its PostgreSQL connection and
  passes psycopg's prepared-statement threshold outside the samples. Server time comes from one
  `explain` (MongoDB, 1 ms resolution) and `EXPLAIN ANALYZE` (PostgreSQL).

The CI job `compare` runs the same correctness gate with `mongo:8.2` and
`postgres:16` service containers. Until its first green run on GitHub it is
informative (`continue-on-error`, 15-minute timeout), not a merge gate. Its timings
are not published.

## Result (2026-11-12, Apple M4 Pro, 12 CPUs, shared with other processes)

All 19 cells returned identical results. The machine was busy during the run (load
average 44.8 at the start, 39.9 at the end; both recorded in the JSON), which
inflates tails, most of all with 8 clients. Neither engine wins across the board:

| Shape | MongoDB p95, 1 / 8 clients | PostgreSQL p95, 1 / 8 clients |
|---|---|---|
| Trees up to 31 companies and hostile shapes (15 cells) | 1.1–1.7 / 2.5–3.4 ms, one outlier at 11.4 ms | 0.3–1.0 / 0.8–2.6 ms |
| 4 levels × 3 children (121 companies) | 2.9 / 5.8 ms | 3.5 / 8.1 ms |
| 6 levels × 2 children (127 companies) | 3.3 / 6.5 ms | 3.8 / 8.2 ms |
| 6 levels × 3 children (1,093 companies) | 25.8 / 58.0 ms | 31.0 / 113.8 ms |
| Hub, 1,200 children | 30.5 / 65.2 ms | 33.3 / 137.2 ms |

- **1 client:** PostgreSQL has the lower p95 in 15 of 19 cells — every group up to
  31 companies and every hostile shape; MongoDB in the four largest (121, 127, 1,093
  and 1,201 companies).
- **8 clients:** the same split, 15 and 4, with wider gaps on the large trees
  (MongoDB at roughly half the PostgreSQL p95). The 11.4 ms MongoDB outlier is
  `arvore_6n_1f`, whose p50 stayed near 2 ms.
- MongoDB carries a fixed cost of about 1 ms per call in this pipeline (seven
  `$lookup`/`$graphLookup` stages, each with a sub-pipeline), visible even on a
  two-company tree. The crossover sits between 31 and 121 companies on these shapes.
- Both stay two orders of magnitude under the protocol's interactive threshold
  (p95 of 3 s).
- An earlier run without the per-worker warm-up timed PostgreSQL connection setup
  inside the 8-client samples and was discarded. With only 5,000 background
  companies PostgreSQL chose different plans and small groups took about 2.2 ms
  instead of 0.3 ms: the planner's view of table size matters, which is why the
  protocol asks for an equivalent background base and expert review of the plans.
- With more than five roots, both sides picked the same five (fixture
  `raizes_com_descendencias`). PostgreSQL orders by id; the MongoDB pipeline slices
  the output of `$setDifference`, whose order the manual does not specify. The
  fixture runs in CI so a change in that order shows up as a divergence.

## Consequences

- The evidence page now shows a measured section instead of "pending", with the
  raw JSON (`frontend/public/evidence/comparison.json`) downloadable.
- What this round does not cover stays listed in the JSON and on the page: Atlas or
  managed PostgreSQL, network, cold cache, the 1.2-million-company dataset,
  concurrency 32 and 64, operational cost, and expert review of both plans.
- 8 concurrent clients are Python threads sharing one interpreter with both
  engines on the same CPU; read that column as contention on one machine, not as
  server capacity.
- The SQL lives in the script, not in the backend: the POV still has one database.
