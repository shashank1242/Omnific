# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is
Omnific is a prompt-to-simulation platform. OpenAI-powered agents build a SimSpec, generated solver code runs in a
sandbox, automated evaluators check the result, a human reviews it, and the user views it in a browser. Read
`docs/architecture.md` before cross-service changes. The other design docs are `docs/service-conventions.md`,
`docs/playback-format.md`, `docs/deployment.md` and `docs/rag.md` (document library, agentic RAG and
clarifying questions). Out-of-scope ideas go in `future.md`; don't build them into the MVP.

## How the pieces connect
- **Request path:** browser → `apps/web`. Its `app/api/[...path]/route.ts` proxies `/api/*` to FastAPI (`services/api`),
  reading `OMNIFIC_API_URL` per request. The API only starts, queries and signals Temporal workflows. It keeps no job
  state of its own, so job state lives in Temporal and files in the ArtifactStore.
- **`SimulationJob`** (`services/workflows/.../simulation.py`):
  - **Intake** (only when the job has attachments, uploaded via `POST /inputs`): sketches/photos are described by vision
    and voice notes transcribed (`MODEL_TRANSCRIBE`). The typed prompt plus that text is the *effective prompt* every
    later agent receives; typed text wins over attachments.
  - **Clarify:** clarifier, then wait for the `clarify` signal.
  - **Research.**
  - **Revision loop** (up to `max_revisions`): modeler → solver-coder → sandbox, run twice (refine=1 and 2) → evaluator.
    Failed checks carry `blame` and send feedback to the modeler or the solver-coder.
  - **Package:** data and the `SceneBuild` child workflow (`scene.py`, mesh v2) run in parallel. SceneBuild's
    **mesh router** (`plan_assets`) gives each entity an ordered list of routes and tries them until one passes:
    `library` (reuse an openly licensed model: Objaverse index / Poly Pizza), `cad` (LLM-written build123d run in
    the sandbox's CAD runner), `generated` (TRELLIS.2 on the GPU worker), `procedural` (always last). Then
    postprocess → rig → animate, every stage evaluated with retries; meshes never fail a job. `mesh_backend` =
    procedural (default) | generated (= the router).
  - **Review:** wait for the `review` signal. A reject starts another round.
  - **Queries** `status`, `draft` and `questions` are how the API and UI see progress.

  `DocumentIngest` (`ingest.py`) runs the librarian for each uploaded document.
- **Workers:** each agent is a separate worker on its own task queue (`TaskQueue` / `ACTIVITY` in
  `omnific_common/contracts.py`), started by `python -m omnific_<agent>` through `omnific_common.temporal.run_worker`.
  Shared code: `omnific_common` (contracts, `llm.py` OpenAI wrapper with `structured`, `run_tools` tool loop,
  `describe_image` and `embed`, `storage.py`, `temporal.py`), `omnific_sim` (spec, primitives, conventions) and
  `omnific_rag` (pgvector store, hybrid search, document `tools.py` used by the researcher and the clarifier).
- **Data between stages:** large payloads go to the ArtifactStore under `jobs/<job_id>/...` (local `.artifacts/`,
  RustFS (S3) in compose, GCS on GKE). Activities pass keys, not bytes. The viewer loads `playback.json` (format in
  `docs/playback-format.md`) and `scene.gltf` through `GET /jobs/{id}/artifacts/{name}`.
- **Sandbox backends** (`SANDBOX_BACKEND`): `subprocess` for local dev, `docker` for compose (host socket, no network),
  `k8s` for GKE (a gVisor Job per run, results returned base64-encoded in the pod log). Two harnesses share them:
  `harness.py` (solver code → `result.npz`, image `SANDBOX_IMAGE`) and `cad_harness.py` (CAD code → `result.zip`,
  image `SANDBOX_CAD_IMAGE`, activity `run_cad`); `SANDBOX_CAD_BACKEND` overrides the backend for CAD only.

## Commands
One uv workspace (root `pyproject.toml`) with one venv. Always use `--no-sync` so concurrent runs don't re-resolve.
```bash
setup.bat [--db] [--check]                                  # Windows cmd: one-shot setup (uv, venv, npm ci, .env)
uv sync --all-packages                                      # after changing any pyproject
uv run --no-sync pytest -q                                  # all Python tests
uv run --no-sync pytest -q agents/modeler -k dimension      # one package / one test
uv run --no-sync ruff check .
uv run --no-sync python scripts/e2e_local.py "<prompt>"     # REAL end-to-end job: in-process Temporal + all
                                                            # workers, real OpenAI (.env), subprocess sandbox
uv run --no-sync python scripts/e2e_local.py "<prompt>" --doc report.pdf --answer id=value
                                                            # + ingest docs (needs `docker compose up -d postgres`)
uv run --no-sync python scripts/e2e_local.py --serve        # same stack, real API on :8000 for the web UI
RAG_TEST_DATABASE_URL=postgresql://omnific:omnific@localhost:5432/omnific_rag_test uv run --no-sync pytest -q
                                                            # pgvector tests; they TRUNCATE, so the DB must end in _test
uv run --no-sync python scripts/make_demo.py                # regenerate apps/web/public/demo (no LLM)
uv run --no-sync python scripts/index_objaverse.py --dry-run --categories car,dog
                                                            # Objaverse index for the library route (drop
                                                            # --dry-run to write it; embedding costs a few cents)
docker compose build cad-runner-build                       # CAD runner image (sandbox/tests/test_cad_harness.py
                                                            # and the CAD route need it; tests skip without it)
cd apps/web && npm run typecheck && npm run lint && npm test && npm run build
docker compose up --build                                   # full local stack (needs Docker Desktop)
```
Local dev notes (Windows):
- The venv is Python 3.11.
- For `--doc` runs and the library, run `docker compose up -d postgres`. For MCP tools, also run
  `docker compose up -d mcp-gateway`; the scripts read the host URLs in `.env`.
- To run the web build without `next start`, copy `.next/static` → `.next/standalone/.next/static` and `public` →
  `.next/standalone/public` first (the Dockerfile does this), then run `node .next/standalone/server.js`.
- terraform and helm were installed with winget and may not be on this shell's PATH: look under
  `%LOCALAPPDATA%\Microsoft\WinGet\Packages\{Hashicorp.Terraform_*,Helm.Helm_*}`.

Terraform (`deploy/terraform/gke`) and Helm (`deploy/helm`) are validated with `terraform validate` and `helm lint`/`helm template`.
The Temporal values file (`deploy/helm/temporal-values.yaml`) was checked key by key against chart 1.6.0. Re-check with
`helm show values temporal/temporal --version <v>` before bumping, because unknown keys are silently ignored.
Never run `terraform apply` or `helm install` without the user's explicit approval: those create billable GCP resources.

## Code review (CodeRabbit)
Review every change set with CodeRabbit before committing: `scripts\coderabbit-review.cmd --agent` (uncommitted
changes; add `--include-untracked` for new files). It runs the official CLI installed in the WSL `Ubuntu` distro
(CodeRabbit also has a native Windows installer; this repo's wrapper uses the WSL install). Fix valid findings, say
which ones you dismissed and why, and re-review if the fixes were substantial. If the CLI reports signed out, ask the
user to run `scripts\coderabbit-review.cmd auth login`.

## Rules that aren't obvious from the code
- In-app agents use **OpenAI** models (the `openai` SDK, Responses API, structured outputs), never the Anthropic SDK.
  Model IDs come only from env vars (`MODEL_REASONING`, `MODEL_GENERAL`, `MODEL_FAST`). The critic (GENERAL) must use
  a different model from the solver-coder (REASONING).
- Contracts live only in `omnific_common.contracts` and `omnific_sim.spec`, and must stay compatible with OpenAI
  strict JSON schema. Check with `openai.lib._pydantic.to_strict_json_schema(Model)`.
- The equation convention (time derivative of X is `dX_dt`) lives in `omnific_sim/conventions.py`. The Modeler's
  validation and the Evaluator must both use it. `agents/evaluator/tests/test_cross_agent.py` guards this.
- Generated solvers define `simulate(spec, refine=1) -> SimResult`. The workflow runs refine=1 and refine=2 for the
  convergence check.
- Always use the shared pint registry `omnific_sim.units.ureg`.
- Mesh v2 runs several activities per queue: they're named in `SceneActivity` (contracts), and
  `GPU_SCENE_ACTIVITIES` routes to the `mesh-gpu` queue. GPU models (TRELLIS.2, UniRig) run as subprocesses in their
  own conda envs inside the GPU image (CUDA 12.8; compose builds only `MESH_GPU_CUDA_ARCHS`, default 12.0); test with
  `MESH_*_BACKEND=fake`. Physics joints come from the SimSpec, never from ML, library rigs or CAD joints (those are
  secondary motion inside an entity).
- The mesh router only offers routes the deployment serves (`MeshCapabilities` from `MESH_GPU_ENABLED`,
  `MESH_CAD_ENABLED`, `MESH_LIBRARY_SOURCES` + their credentials), so it never waits on a missing worker. Route
  outcomes per kind go to `mesh_route_stats` (`omnific_rag.route_stats`, best-effort).
- Library models: only CC0 and CC-BY (NC, ND, SA and unknown are rejected in `omnific_mesh/library/licence.py`), and
  every reused model's attribution must reach `scene.gltf` (`asset.copyright`, node extras) and the report.
  Downloads go through `library/fetch.py` only (HTTPS, public hosts checked on every redirect, size cap, GLB magic)
  and are re-encoded by our own GLB writer. Sketchfab is deliberately absent (its API needs per-user OAuth).
- Generated CAD code is like solver code: it runs only in the CAD runner (`run_cad` on the sandbox queue). The mesh
  worker's `cad_write` only writes it and `cad_evaluate` only reads the runner's output.
- Generated solver code runs only in the sandbox (`sandbox/`). Never `exec` it in an agent process. The
  `subprocess` backend is not isolated and is for local dev only.
- The workflow calls activities by name (`ACTIVITY[TaskQueue.X]`) and must not import agent code.
- `.env` holds the real OpenAI key and is gitignored. `.env.example` must only ever contain placeholders. The
  user's Windows environment has a stale `OPENAI_API_KEY`, so scripts should let `.env` win.
- On Windows, write files with `encoding="utf-8"` explicitly. The default cp1252 corrupts non-ASCII characters.
- Async psycopg (pgvector) can't use Windows' Proactor loop: entry points use `omnific_common.aio.run`, and the root
  `conftest.py` switches pytest to a selector loop.
- Clarification precedence: explicit user answer > attached documents > default. A default must never override a
  document value (the critic will rightly fail it).
- Researcher MCP tools: the curated `MCP_TOOL_ALLOWLIST`, and `DENYLIST` in `mcp_tools.py` (Sci-Hub/LibGen) always wins.
  Test MCP code against real `mcp.types` objects: the SDK renamed fields between major versions.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
