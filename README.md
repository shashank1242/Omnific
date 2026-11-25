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
and Docker MCP research tools. Scenes are dressed in Blender (materials, scenery, lights and CC0 props, never the
motion) and every run downloads as a `.blend`; with generated meshes the user picks each object's 3D model (Sketchfab,
Poly Haven, Objaverse, Poly Pizza; CC0 / CC-BY only) while the simulation builds.
See `docs/architecture.md` for the design, `docs/rag.md` for documents and questions, and `future.md` for what is
deliberately out of scope.

## Layout
| Path | What |
|---|---|
| `packages/omnific-sim/` | SimSpec schema, SimResult, solver primitives, evaluator checks (shared by every agent) |
| `agents/` | One container per agent: intake, clarifier, librarian, researcher, modeler, solver_coder, data, mesh, mesh_gpu, evaluator, dress (Blender scene dressing) |
| `packages/omnific-rag/` | pgvector document store, hybrid search and the document tools for agents |
| `services/api/` | FastAPI gateway — milestone 2 |
| `services/workflows/` | Temporal workflow definitions — milestone 2 |
| `sandbox/` | Locked-down runners for generated code: solver, CAD (build123d) and Blender (`bpy`) |
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

### Blender, model libraries and browser tests
- Scene dressing needs the Blender runner: `docker compose build blender-runner-build` (compose), or for
  `scripts/e2e_local.py` and the harness tests a Python 3.11 with `bpy` in `BLENDER_PYTHON`
  (`uv venv .venv-blender --python 3.11` then `uv pip install --python .venv-blender bpy==5.0.1`).
  `DRESS_ENABLED=0` turns dressing off.
- Model libraries: `MESH_LIBRARY_SOURCES=sketchfab,polyhaven` (Poly Haven needs no key; Sketchfab needs your
  personal `SKETCHFAB_API_TOKEN`, single-user deployments only).
- Browser end-to-end tests (Playwright, drives the installed Edge by default): with the site running,
  `cd apps/web && npm run e2e` (`E2E_BASE_URL`, default http://localhost:3000; `E2E_CHANNEL=chrome` elsewhere).

