# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is
Omnific is a prompt-to-simulation platform. OpenAI-powered agents build a SimSpec, generated solver code runs in a
sandbox, automated evaluators check the result, a human reviews it, and the user views it in a browser. Read
`docs/architecture.md` before cross-service changes. The other design docs are `docs/service-conventions.md`,
`docs/playback-format.md` and `docs/deployment.md`. Out-of-scope ideas go in `future.md`; don't build them into the MVP.

## Commands
One uv workspace (root `pyproject.toml`) with one venv. Always use `--no-sync` so concurrent runs don't re-resolve.
```bash
uv sync --all-packages                                      # after changing any pyproject
uv run --no-sync pytest -q                                  # all Python tests
uv run --no-sync pytest -q agents/modeler -k dimension      # one package / one test
uv run --no-sync ruff check .
uv run --no-sync python scripts/e2e_local.py "<prompt>"     # REAL end-to-end job: in-process Temporal + all
                                                            # workers, real OpenAI (.env), subprocess sandbox
uv run --no-sync python scripts/e2e_local.py --serve        # same stack, real API on :8000 for the web UI
uv run --no-sync python scripts/make_demo.py                # regenerate apps/web/public/demo (no LLM)
cd apps/web && npm run typecheck && npm run lint && npm test && npm run build
docker compose up --build                                   # full local stack (needs Docker Desktop)
```
Terraform (`deploy/terraform/gke`) and Helm (`deploy/helm`) are validated with `terraform validate` and `helm lint`/`helm template`.
Never run `terraform apply` or `helm install` without the user's explicit approval: those create billable GCP resources.

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
- Generated solver code runs only in the sandbox (`sandbox/`). Never `exec` it in an agent process. The
  `subprocess` backend is not isolated and is for local dev only.
- The workflow calls activities by name (`ACTIVITY[TaskQueue.X]`) and must not import agent code.
- `.env` holds the real OpenAI key and is gitignored. `.env.example` must only ever contain placeholders. The
  user's Windows environment has a stale `OPENAI_API_KEY`, so scripts should let `.env` win.
- On Windows, write files with `encoding="utf-8"` explicitly. The default cp1252 corrupts non-ASCII characters.
