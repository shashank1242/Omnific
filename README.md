# Omnific

Prompt-to-simulation platform. A user describes anything ("a pizza baking on Venus"), and a pipeline of
OpenAI-powered agents researches it, builds a physically grounded model, solves it numerically, checks the
result with automated evaluators, and hands it to a human reviewer before the user sees it in a browser
viewer (game-engine export later).

**Accuracy comes from the solver layer, not the renderer.** Every result is labelled with its fidelity
(analytic / validated numerical / heuristic), its assumptions and its cited sources.

## Status
MVP complete (milestones 1–5), plus articulated bodies (playback v2), a document library with agentic RAG
(PDF/DOCX/PPTX/XLSX/CSV/HTML/MD/images, figures read by a vision model), clarifying questions before research,
and Docker MCP research tools.
See `docs/architecture.md` for the design, `docs/rag.md` for documents and questions, and `future.md` for what is
deliberately out of scope.

## Layout
| Path | What |
|---|---|
| `packages/omnific-sim/` | SimSpec schema, SimResult, solver primitives, evaluator checks (shared by every agent) |
| `agents/` | One container per agent: intake, clarifier, librarian, researcher, modeler, solver_coder, data, mesh, mesh_gpu, evaluator |
| `packages/omnific-rag/` | pgvector document store, hybrid search and the document tools for agents |
| `services/api/` | FastAPI gateway — milestone 2 |
| `services/workflows/` | Temporal workflow definitions — milestone 2 |
| `sandbox/` | Locked-down runner for generated solver code — milestone 2 |
| `apps/web/` | Next.js + Three.js viewer and review UI — milestone 4 |
| `exporters/` | Browser bundle, later Godot / Summer Engine — milestone 4+ |
| `deploy/helm/` | Helm charts + Terraform for GKE/EKS/AKS — milestone 5 |

## Develop
Windows, from `cmd` in the repo root (installs uv if missing; needs Node.js LTS, Docker optional):
```bat
setup.bat              rem Python 3.11 workspace + web deps + .env from .env.example
setup.bat --db         rem also start Postgres (pgvector) for the document library
setup.bat --check      rem also run ruff, pytest and the web checks
```
Then set `OPENAI_API_KEY` in `.env`. Elsewhere: `uv sync --all-packages`, `cd apps/web && npm ci`,
`cp .env.example .env`. Commands for tests, end-to-end runs and the stack are in `CLAUDE.md`.
