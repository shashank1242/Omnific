# Omnific

Prompt-to-simulation platform. A user describes anything ("a pizza baking on Venus"), and a pipeline of
OpenAI-powered agents researches it, builds a physically grounded model, solves it numerically, checks the
result with automated evaluators, and hands it to a human reviewer before the user sees it in a browser
viewer (game-engine export later).

**Accuracy comes from the solver layer, not the renderer.** Every result is labelled with its fidelity
(analytic / validated numerical / heuristic), its assumptions and its cited sources.

## Status
MVP, milestone 1: the shared `omnific-sim` package (SimSpec contract, solver primitives, evaluator checks).
See `docs/architecture.md` for the design and `future.md` for what is deliberately out of MVP scope.

## Layout
| Path | What |
|---|---|
| `packages/omnific-sim/` | SimSpec schema, SimResult, solver primitives, evaluator checks (shared by every agent) |
| `agents/` | One container per agent (researcher, modeler, solver_coder, data, mesh, evaluator) — milestone 2 |
| `services/api/` | FastAPI gateway — milestone 2 |
| `services/workflows/` | Temporal workflow definitions — milestone 2 |
| `sandbox/` | Locked-down runner for generated solver code — milestone 2 |
| `apps/web/` | Next.js + Three.js viewer and review UI — milestone 4 |
| `exporters/` | Browser bundle, later Godot / Summer Engine — milestone 4+ |
| `deploy/helm/` | Helm charts + Terraform for GKE/EKS/AKS — milestone 5 |

## Develop
```bash
cd packages/omnific-sim
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows; use .venv/bin/python on Linux/macOS
.venv/Scripts/python -m pytest -q
```
