# Architecture

## Pipeline (one Temporal workflow per simulation job)
```
Web UI (Next.js + Three.js viewer, review screen)
   │ REST/WebSocket
API gateway (FastAPI) ── Temporal (job state, reviews) ── object storage (results, scenes)
   │
Temporal workflow: SimulationJob
 -1. Intake       → only with attachments: vision describes sketches/photos, speech-to-text transcribes voice notes;
                    the typed prompt + that text becomes the effective prompt every later agent reads (typed text wins)
  0. Clarifier    → reads attached documents, asks only what they don't answer; waits for answers (defaults on timeout)
  1. Researcher   → agentic: searches the user's documents (pgvector), MCP research tools and the web; cited facts
  2. Modeler      → SimSpec (packages/omnific-sim/src/omnific_sim/spec.py)
  3. Solver-coder → Python against omnific_sim.primitives
  4. Sandbox      → runs the generated code (gVisor, no network, CPU and time limits) → SimResult
  5. Data         → keyframe sampling and object-change streams for playback
  6. Mesh         → SceneBuild child workflow (mesh v2, below): per-entity generated/rigged/animated meshes, each
                    stage evaluated, falling back to the procedural asset library
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
  per role: `MODEL_REASONING` (Modeler, Solver-coder), `MODEL_GENERAL` (Researcher, critic, Intake vision),
  `MODEL_FAST` and `MODEL_TRANSCRIBE` (Intake speech-to-text).
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

## Mesh v2: SceneBuild
`SimulationJob` packaging runs the `SceneBuild` child workflow (`services/workflows/.../scene.py`) next to the Data
agent. `JobRequest.mesh_backend` (API: `mesh_backend` in `POST /jobs`, default `MESH_BACKEND`) picks the path:
`procedural` reproduces the MVP scene byte for byte; `generated` runs, per entity and in parallel:

| Stage | Worker | Evaluated by |
|---|---|---|
| plan_assets | mesh (`MODEL_GENERAL`) | plan repair: coverage, physics rig for every `arm`, clips only with an ML rig, budgets |
| generate | mesh-gpu: OpenAI reference image (`MODEL_IMAGE`) → TRELLIS.2 | validity (finite, degenerate faces, components, appearance) + vision critic on a 4-view render |
| postprocess | mesh | poly budget, Hausdorff < 2 % vs the raw mesh, winding, size = the replaced primitive's, centred, texture ≤ 1024² |
| physics_rig | mesh | joint = spec pivot/arm × length scale, rest pose, rigid swing, hierarchy (viewer articulation math) |
| ml_rig | mesh-gpu: UniRig | tree skeleton, weights valid/sum to 1/≤ 4 influences, bones inside the mesh, ±30° deformation stretch < 1.5×, vision critic |
| animate | mesh-gpu (no model by default) → mesh procedural clips | duration, unit quaternions, joint limits, smoothness, loop closure, no root motion, vision critic on frames |
| assemble_scene | mesh | every entity has a root node, glTF structure, size ≤ 50 MB, clips declared |

A failed evaluation retries that stage with the critique (`max_attempts`, default 3; a failed post-process
regenerates). A stage that never passes drops the entity a level: generated+rigged → generated → procedural; a GPU
stage with no worker (schedule-to-start timeout) or no backend (`StageUnavailable`) falls back at once. A broken
assembled scene falls back to all-procedural. Meshes never fail a job. Every decision is in `JobOutcome.scene_report`
(web: "3D assets" tab). Finished generated assets are cached across jobs under `assets/<hash>/`.

Physics stays authoritative: articulated entities rotate about their spec joint from the solver's angle tracks; ML
skeletons and clips are secondary motion only (root translation zeroed; a clip's `drive_state` scales its rate).
The scene contract is in `docs/playback-format.md` ("scene.gltf v3 extras"). The GPU worker
(`agents/mesh_gpu`) runs one activity at a time; TRELLIS.2 needs Linux and a ≥ 24 GB GPU (the GKE `gpu` pool).
`MESH_*_BACKEND=fake` swaps the models for CPU stand-ins so the whole path runs (and is tested) without a GPU.
