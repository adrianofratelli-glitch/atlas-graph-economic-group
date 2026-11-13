# Graph analytics on MongoDB Atlas — economic groups and visibility hierarchies

Mapa do código: [docs/MAPA_DO_CODIGO.md](docs/MAPA_DO_CODIGO.md)

A credit applicant has no overdue balance. Its economic group tells a different story: 43 companies, R$ 154.5 million in limits and R$ 10.7 million overdue elsewhere in the ownership chain.

This POV connects those records inside MongoDB. One aggregation walks up to corporate controllers, identifies valid roots, walks down through their holdings and joins credit exposure. A second scenario derives an advisor or manager's commercial scope from a reporting hierarchy.

The dataset is synthetic: 1.2 million companies and about 2.5 million ownership edges. The interface is in Brazilian Portuguese. No customer data is included.

## What the evidence supports

The workload is a bounded ownership investigation entered by a business key. It demonstrates that these traversals, exposure reads, transactional reviews and notifications can share one operational database. It does not establish that MongoDB outperforms a dedicated graph engine or a relational recursive query.

Two relationship models are used deliberately:

| Scenario | Model | Question |
|---|---|---|
| Ownership | `ownership` edge collection, with percentage and role | Which companies belong to this ownership group? |
| Commercial hierarchy | `advisors.reports_to` | Which accounts fall within this profile's reporting chain? |

Dense exploratory networks, unrestricted shortest paths and continuous graph algorithms require a separate evaluation. See [limitations](LIMITATIONS.md).

## Follow an investigation

Select a company and increase the ownership depth. The summary connects the applicant's individual exposure to the group and highlights paths to companies in arrears. A partial result is labelled and cannot open a group review.

![Economic group and evidence summary](docs/screenshots/02-grupo-economico.png)

Opening a review revalidates the investigation in a transaction and records the group and exposure on the server. Replaying the same valid proof returns the same open case; another investigation overlapping an open case receives a conflict. Closing a review is also idempotent. Change Streams feed the corresponding alerts.

The name search is scoped to the displayed graph unless the presenter explicitly opens the search to the whole database. The visibility selector demonstrates hierarchy reachability, not authentication. Semantic concentration is an optional panel comparing activities with the one carrying the largest individual exposure; it does not count every distinct business.

## Check the result, then inspect the limit

Open **Evidências técnicas** in the header, or visit `/evidence`.

The first section compares six showcase groups against their original generator manifests. Membership, internal corporate edges and credit totals are verified independently of the traversal. The page includes per-company amounts, a path to a distressed company, source hashes and server execution statistics.

![Independent membership and exposure reconciliation](docs/screenshots/08-investigacao-auditavel.png)

The second section varies depth and branching in a temporary Atlas database. It reports raw samples, p50, p95, edge matches in each direction, returned edges and completeness. A 1,200-child case exposes the response cap. These small controlled fixtures are separate from the 1.2-million-company dataset; their results are not a capacity forecast.

![Measured depth and branching curve, with the presentation limit](docs/screenshots/09-curva-de-comportamento.png)

The measurements are a dated snapshot, available as [JSON](frontend/public/evidence/results.json). Run them again with:

```bash
backend/venv/bin/python queries/build_evidence.py --runs 10
npm --prefix frontend run build
```

The script reads the demo dataset and creates an isolated `graph_evidence_test_<uuid>` database for the curve, then removes it. Ten warm samples per point provide an exploratory curve, not a stable production tail-latency estimate.

The third section compares the same question against PostgreSQL `WITH RECURSIVE`, with both engines running locally on one machine and no network in between. **It is a preliminary measurement under load** (load average 44.8 → 39.9 on 12 CPUs, other processes running). To repeat it on an idle machine: wait until `os.getloadavg()` is below about 2, then run `compare_postgres.py --runs 100 --concurrency 1,8`. A cell only gets timings once both engines return the same companies, edges, cents and reach reasons; all 19 did. With 1 client and with 8, PostgreSQL had the lower p95 in the same 15 cells (groups of up to 31 companies and every hostile shape) and MongoDB in the four largest trees (121 to 1,201 companies). With 8 clients PostgreSQL's p95 was about twice MongoDB's on the 1,093- and 1,201-company trees and about 1.3–1.4× on the 121- and 127-company ones. Both stayed far below the 3-second interactive threshold. Managed services, network, cold cache and the full 1.2-million-company base are not part of this round. See [ADR 0004](docs/adr/0004-first-postgresql-comparison-on-local-engines.md) and the [comparison protocol](docs/comparison-protocol.md).

![Same result in both engines, timings per tree shape](docs/screenshots/10-comparacao-postgresql.png)

```bash
python3.12 -m venv /tmp/cmp && /tmp/cmp/bin/pip install -r queries/requirements-compare.txt
/tmp/cmp/bin/python queries/compare_postgres.py --runs 100 --concurrency 1,8   # needs mongod on PATH
```

The name search stays lexical (exact, fuzzy and autocomplete in one `compound`), and the semantic panel stays an exact vector search over 32 activities. [ADR 0005](docs/adr/0005-no-rank-fusion-in-name-resolution.md) explains why neither uses `$rankFusion`.

## What it takes to run

- A MongoDB Atlas cluster configured with the Search and Vector Search indexes
  used here. A plain local mongod without the required search services leaves
  those panels unavailable; this repository does not provision local Search.
- Python 3.11 or newer, and Node 18 or newer.
- A Voyage API key for the embeddings. Without it, only the semantic panel is
  unavailable.

## Installation

```bash
cp .env.example .env      # fill in MONGODB_URI and VOYAGE_API_KEY

python3 -m venv .venv && .venv/bin/pip install -r data-generator/requirements.txt
python3 -m venv backend/venv && backend/venv/bin/pip install -r backend/requirements.txt
(cd frontend && npm install)
```

## Generating the data

One command rebuilds the whole demo state — people, ownership, commercial
hierarchy, B-tree indexes, review state, activity embeddings, Atlas Search and
Vector Search indexes (it waits for `READY`) — and then verifies counts,
traversal indexes, index status and a reference `$graphLookup`:

```bash
ALLOW_DEMO_DB_WRITE=1 .venv/bin/python scripts/reset_demo.py
```

It refuses any database whose name does not end in `_test` unless
`ALLOW_DEMO_DB_WRITE=1` is set; every script in `data-generator/` applies the
same guard. To validate the pipeline without touching the demo, point it at a
test database with the reduced scale (20,000 people, 30,000 companies, 1,000
groups and the same 40 showcase groups):

```bash
MONGODB_DB=graph_grupo_economico_test .venv/bin/python scripts/reset_demo.py --scale small --drop
```

`bash data-generator/run_all.sh` is kept as a shortcut to the same script.
Volume can be overridden with `PEOPLE`, `COMPANIES`, `ECON_GROUPS` and
`SHOWCASE`. Do not use `GROUPS`: it is a special read-only variable in bash, and
earlier versions of `run_all.sh` silently generated 20 groups because of it.
Without `VOYAGE_API_KEY` the embedding step is skipped and only the semantic
panel is unavailable.

The defaults are 800,000 individuals, 1.2 million companies, 40,000 economic groups
and a 969-person commercial hierarchy.

Running it again duplicates nothing: every document has an identifier derived from
its own content, so a second run rewrites the same records.

The population of individuals is a modelling decision, not a volume one. With
1.2 million companies and about 2.2 shareholders each, a population of 150,000 puts
the same person in ~17 companies — "shared shareholder" stops being an exception and
becomes a property of every pair of companies in the base.

## Running

```bash
./start.sh               # UI on 5350, API on 8350
DEV=1 ./start.sh     # with hot reload, for development
```

## Docker

One container: nginx serves the built UI on port 8080 and proxies `/api` and `/health` (including the
`/api/alerts/stream` SSE feed) to the FastAPI backend, which runs with a single worker on `127.0.0.1:8000`.

```bash
docker build -t atlas-graph-economic-group .
docker run --env-file .env.docker -p 8080:8080 atlas-graph-economic-group
curl -fsS http://127.0.0.1:8080/health      # connection, counts, search index status
```

Prebuilt image (published to GHCR on every push to `main` that touches the image):

```bash
docker pull ghcr.io/adrianofratelli-glitch/atlas-graph-economic-group:latest
docker run --env-file .env.docker -p 8080:8080 ghcr.io/adrianofratelli-glitch/atlas-graph-economic-group:latest
```

Same `--env-file` rule as above (`KEY=value`, no quotes); tags: `latest` and `sha-<7-char commit>`.

`docker run --env-file` does not strip quotes: write `KEY=value` with no quotes in the file you
pass (a quoted `MONGODB_URI` reaches the driver with the quotes and fails). No `.env` and no secret go into
the image; everything is read from the environment at runtime.
Loading or resetting data stays outside the image (`scripts/reset_demo.py`).

## Measurements and resilience

Current validation and load results are described in [the validation report](docs/resilience-validation.md). The older latency and ingestion measurements remain in [queries/benchmarks.md](queries/benchmarks.md), labelled with their original environment and date. They do not describe the latency of the current network path or prove a before/after gain for this release.

```bash
backend/venv/bin/python -m unittest discover -s tests -p 'test_*.py'   # offline, no Atlas
node --test frontend/tests/*.test.mjs
backend/venv/bin/python tests/test_resilience.py --quick   # needs ./start.sh; read-only
backend/venv/bin/python tests/http_adversarial.py          # needs ./start.sh; read-only
backend/venv/bin/python tests/live_hardening.py            # own graph_resilience_test_<uuid>
backend/venv/bin/python tests/live_graph_adversarial.py    # own graph_adversarial_test_<uuid>
backend/venv/bin/python tests/live_reset_race.py           # own graph_resetrace_test_<uuid>
backend/venv/bin/python tests/stress.py --max 64 --seconds 20
```

The API-level scripts read `API_BASE` and the browser scripts `UI_BASE`, so a copy on free ports can be tested without touching the presenter's 8350/5350: run the backend with `MONGODB_DB=graph_grupo_economico_test` on another port and the UI with `GRAPH_API_TARGET=http://127.0.0.1:<api port> npx vite preview --port <ui port>`.

`live_graph_adversarial.py` builds hostile topologies in a throwaway database —
a hub with 5,000 children, a three-node cycle, a self-loop, a 20-level chain,
an edge to a missing owner, Unicode names — and checks that each traversal
terminates, respects the node and depth caps, declares partial coverage, turns
an exhausted `GRAPH_MAX_TIME_MS` into a 503 with a hint, and stays deterministic
under 40 concurrent requests. It also measures how long a freshly written edge
takes to appear in the next traversal. `http_adversarial.py` covers oversized
id lists, unknown fields, Mongo operators, NUL/RTL/zero-width input, CORS and
45 simultaneous alert streams.

`GRAPH_MAX_TIME_MS` is applied with `pymongo.timeout()`: with the client-level
`timeoutMS`, the driver ignores a per-operation `maxTimeMS`.

The live write suite creates and removes its own database. It does not reset the demonstration dataset. Browser tests cover the usual flow, narrow screens, delayed responses, duplicate clicks, unavailable panels and recovery after disconnection.

Analytical queries have bounded concurrency and may return 429 under saturation. Timings that include these refusals are identified in the report. The tests establish the cases exercised, not immunity to every failure.

`GET /health` checks connectivity, index readiness and a reference traversal. `POST /api/demo/reset` changes the demonstration dataset; it is an explicit presenter action, not part of the read-only checks.

The reset is several writes, not one transaction, so it holds a lease (`demo_control/review_lock`, 120 s, self-expiring). Opening or closing a review writes to that same document inside its transaction: while a reset runs it is refused with 409 `reset_in_progress`; a review that committed first is cleaned by the reset that follows. The reset ends by sweeping any company or exposure still flagged without a case. Before this, a review opened in a second tab mid-reset left the whole group blocked with no case to close (43 companies on the small dataset). `tests/live_reset_race.py` replays that schedule, the reverse order, an expired lease and 12 random interleavings. See [the demo script](docs/demo-script.md).

## What this does not solve

[LIMITATIONS.md](LIMITATIONS.md) opens with the scope this project chose — a shallow
tree loaded in volume — and then lists the technical limits inside it: `$graphLookup`
has memory and result-size limits; sharded traversal was not benchmarked, and
traversal inside the review transaction requires an unsharded target. No native
PageRank or community-detection algorithm is demonstrated.

## Where everything is

| Folder | Contents |
|---|---|
| `data-generator/` | people, ownership base, commercial hierarchy, embeddings |
| `schema/` | indexes and collection modelling |
| `queries/` | the benchmark script and the measured results it wrote |
| `backend/` | the FastAPI API; every query lives in `app/db/` |
| `frontend/` | the React interface |
| `tests/` | the hostile suite and the mixed-workload stress |
| `docs/` | technical briefing, architecture decision records, demo script and business case |

To understand how it was built, start with
[implementation_plan.md](implementation_plan.md).


## Investigation evidence and resilience

The **Resumo** panel connects the applicant's arrears to the companies and links
that explain the consolidated exposure. Technical drawers show the submitted
group pipeline and the executed exact vector-search pipeline (vector omitted).
Application timings include network and are not `explain` output.

Coverage is explicit: depth frontiers with unvisited edges, unresolved roots,
edge/root caps, missing entities and the display node cap disable group review.
The API accepts `investigation_token` and `reason` for review. Browser-provided
company IDs, totals and analyst identities are rejected. The token expires in
15 minutes; refresh the query after expiry or a process restart. Multi-worker
hosting requires a shared `INVESTIGATION_SIGNING_KEY`.

The visibility selector is a demonstration of hierarchy scope, not authentication
or API authorisation. The semantic panel measures similarity to the activity with
the largest individual credit exposure, not the number of distinct businesses.

Local regression and real Atlas isolation tests:

```bash
backend/venv/bin/python tests/test_hardening.py
backend/venv/bin/python -m unittest discover -s tests -p test_client_concurrency.py
(cd frontend && node --test tests/*.test.mjs && npm run build)
backend/venv/bin/python tests/live_hardening.py
```

The last command creates and removes its own `graph_resilience_test_<uuid>`
database. It does not reset the demo dataset. See `docs/resilience-validation.md`
for the validation scope and remaining limits.

## License

MIT, see [LICENSE](LICENSE).
