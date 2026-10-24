# Architecture

## Pipeline (one Temporal workflow per simulation job)
```
Web UI (Next.js + Three.js viewer, review screen)
   │ REST/WebSocket
API gateway (FastAPI) ── Temporal (job state, reviews) ── object storage (results, scenes)
   │
Temporal workflow: SimulationJob
  1. Researcher   → constants and material properties, with citations
  2. Modeler      → SimSpec (packages/omnific-sim/src/omnific_sim/spec.py)
  3. Solver-coder → Python against omnific_sim.primitives
  4. Sandbox      → runs the generated code (gVisor, no network, CPU and time limits) → SimResult
  5. Data         → keyframe sampling and object-change streams for playback
  6. Mesh         → glTF scene from the asset library
  7. Evaluators   → all must pass; failures loop back to step 2 or 3 (bounded retries)
  8. Human review → Temporal signal: approve, or reject with notes (notes feed back to the agents)
  9. Exporters    → browser bundle (engine export later, see future.md)
```

## Key decisions
- **The solver is the source of truth.** Engines and the viewer only play back SimResult. Game-engine physics
  is never used for accuracy.
- **Code generation over fixed templates.** "Simulate anything" works because the Solver-coder writes code
  against a small, tested primitive library, and that code always runs in the sandbox.
- **Honest fidelity.** Every SimSpec carries `fidelity`, `assumptions` and `citations`, and the UI shows them.
- **SimSpec is the contract.** Agents talk through SimSpec and SimResult only. Any schema change must stay
  compatible with OpenAI structured outputs (`SimSpec.model_json_schema()`).
- **LLMs are OpenAI only**, via the `openai` SDK (Responses API, structured outputs). Model IDs come from env vars
  per role: `MODEL_REASONING` (Modeler, Solver-coder), `MODEL_GENERAL` (Researcher, critic) and `MODEL_FAST`.
  The critic must use a different model from the Solver-coder.
- **Temporal owns orchestration** (retries, evaluation loops, human waits). The OpenAI Agents SDK does not run the workflow.
- **One Kubernetes cluster, one namespace per agent**, with separate node pools (cpu, gpu, sandbox-gvisor).
  Do not use one cluster per agent.

## Evaluators (all run before human review)
Deterministic first: dimensional consistency, finiteness, bounds, conservation drift, convergence (refine=1 vs
refine=2) and analytic spot checks (`SimSpec.analytic_checks`). Only if those all pass does the LLM critic run
(role `MODEL_GENERAL`). Its major issues fail the report and its minor issues are informational. Each failing check carries a
`blame`, and the workflow routes its feedback to the Modeler or the Solver-coder.

The equation convention (`dX_dt` for time derivatives, allowed functions and constants, dimension-check exemptions)
lives in `omnific_sim/conventions.py`, shared by the Modeler's validation and the Evaluator.

## Milestones
1. `omnific-sim`: SimSpec, SimResult, primitives, checks, tests.
2. Agents plus Temporal workflow, run locally with docker-compose.
3. Evaluator suite plus human review step.
4. Browser viewer.
5. Helm and Terraform for GKE/EKS/AKS, plus CI.
