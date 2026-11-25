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
  6. Mesh         → SceneBuild child workflow (mesh v2, below): a router picks per entity how to make its asset
                    (model library, CAD, 3D generation), each stage evaluated, falling back to the procedural library
  7. Evaluators   → all must pass; failures loop back to step 2 or 3 (bounded retries)
  8. Human review → Temporal signal: the creator accepts, or asks for changes in plain words (sent as a reject
                    with blame=modeler; the words feed back to the Modeler)
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

## Environments and custom answers
Every SimSpec has an `environment` (lab bench, park, the Moon...): the clarifier always asks about the setting unless
the prompt names one, every question accepts a typed custom answer (`ClarifyAnswer.custom`, numbers may carry their
own unit), and the modeler turns the setting into ordinary cited parameters listed in `environment.parameters`. The
evaluator checks each of them reaches the equations (`environment[...]`), so a Moon setting really changes g. The
playback carries the environment's look; the viewer draws sky, ground, fog and a light rig under the entities.

## Finished jobs, caching and the public library
- **Snapshots.** The first time the API reads a finished job's outcome it writes `jobs/<id>/outcome.json` to the
  ArtifactStore. From then on the job page and its artifacts are served from that snapshot (plus small in-process
  LRUs: `OUTCOME_CACHE_ITEMS`, `ARTIFACT_CACHE_MB`) without asking Temporal, so a finished simulation outlives its
  Temporal history.
- **HTTP caching.** A finished job's artifacts are `Cache-Control: immutable`; a draft's are `no-cache` with an
  ETag (the same URL changes when the creator asks for changes). Responses over 1 KB are gzipped.
- **Public library (opt-in).** The creator of an accepted simulation can publish it (`POST /jobs/{id}/publish`,
  with a JPEG of the current 3D view as `jobs/<id>/thumbnail`). The listing lives in Postgres
  (`omnific_rag.gallery`, table `published_simulations`); `GET /gallery?q=` searches it. There are no accounts yet,
  so anyone with the link can publish or unpublish.

## Variants: change values and compare (`SimulationVariant`)
The Tune tab lets the user edit any parameter, initial value or the end time of a finished (or awaiting-review)
simulation and re-run it. `POST /jobs/{id}/variants` applies the changes to the job's SimSpec (`apply_changes`,
validated: known names, finite numbers, end after start) and starts the `SimulationVariant` workflow
(`services/workflows/.../variant.py`): the job's own solver in the sandbox, then playback. No LLM is involved, so a
run takes seconds, and it is not re-evaluated (the UI says so). This only works because solvers must read every value
from the spec and parameters must be independent inputs (modeler prompt): a stored derived value would go stale.
Variant artifacts live under `jobs/<id>/variants/<vid>/` (record `variant.json`, index `variants/index.json`); the
API refreshes a running record from its workflow. The web app shows any run in the viewer and overlays another run's
charts (dashed) with a table of how each quantity's end and peak values moved.

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
`procedural` reproduces the MVP scene byte for byte; `generated` runs the **mesh router**, then each entity's routes
in parallel.

**Router** (`plan_assets`, `agents/mesh/src/omnific_mesh/plan.py`): an ordered list of routes per entity, tried until
one passes its checks, always ending at `procedural`:

| Route | For | How |
|---|---|---|
| `library` | detailed real-world objects people have already modelled (cars, animals, furniture) | search Objaverse (local pgvector index, `scripts/index_objaverse.py`) / Poly Pizza → licence filter (CC0, CC-BY only) → vision shortlist on thumbnails → safe download (HTTPS, public hosts, size cap, GLB only, re-encoded by our writer) |
| `cad` | simple mechanisms (arms, wheels on axles, pistons, gears) | `MODEL_REASONING` writes a build123d script → the sandbox's CAD runner runs it (`run_cad`, `sandbox/cad-runner.Dockerfile`, never a worker) → parts, joints, interference at rest and across every joint's range |
| `generated` | organic or unusual objects nobody has modelled | TRELLIS.2 on the GPU worker (below) |
| `procedural` | primitives, and the final fallback | the MVP asset library |

The router only offers routes this deployment serves (`MeshCapabilities`: `MESH_GPU_ENABLED`, `MESH_CAD_ENABLED`,
`MESH_LIBRARY_SOURCES` + credentials) and its rules are deterministic after the LLM (primitives stay procedural,
unavailable routes dropped, `cad` ahead of `generated` for articulated entities). Each route's outcome per entity kind
goes to `mesh_route_stats` (Postgres) and back into the router prompt, so routing improves with use.

| Stage | Worker | Evaluated by |
|---|---|---|
| plan_assets | mesh (`MODEL_GENERAL`) | plan repair: coverage, routes valid/available, physics rig for every `arm`, clips only with an ML rig or CAD, budgets |
| library_search / fetch | mesh (network) | ≥ 1 licensed candidate, vision shortlist, download safety, GLB parse; then the generate checks + vision critic (`library` stage) |
| library_rig | mesh | a model that arrived rigged keeps its skeleton only if it passes the ml_rig checks; its own clips matched to planned ones by name/kind, root motion removed |
| cad_write → run_cad → cad_evaluate | mesh (`MODEL_REASONING`) → sandbox (CAD runner) → mesh | script runs; parts as designed; valid solids; no overlap (≤ 5 % at a moving joint, ≤ 1 % otherwise) at rest and across joint ranges; mesh validity; rig from joints (skin checks, zero deformation stretch); kinematic clips (spin / oscillate) pass the clip checks; vision critic. Failures go back to the writer with its previous code |
| generate | mesh-gpu: OpenAI reference image (`MODEL_IMAGE`) → TRELLIS.2 | validity (finite, degenerate faces, components, appearance) + vision critic on a 4-view render |
| postprocess | mesh | poly budget, Hausdorff < 2 % vs the raw mesh, winding, size = the replaced primitive's, centred, texture ≤ 1024². Authored models (library, CAD) keep every component and are only decimated above 100k triangles; inconsistent winding doesn't block them. Skins survive postprocess |
| physics_rig | mesh | joint = spec pivot/arm × length scale, rest pose, rigid swing, hierarchy (viewer articulation math) |
| ml_rig | mesh-gpu: UniRig | tree skeleton, weights valid/sum to 1/≤ 4 influences, bones inside the mesh, ±30° deformation stretch < 1.5×, vision critic |
| animate | mesh-gpu (no model by default) → mesh procedural clips | duration, unit quaternions, joint limits, smoothness, loop closure, no root motion, vision critic on frames |
| assemble_scene | mesh | every entity has a root node, glTF structure, size ≤ 50 MB, clips declared |

A failed evaluation retries that stage with the critique (`max_attempts`, default 3; a failed post-process
regenerates). A route that never passes hands the entity to its next route; within a route a rig or clip stage that
never passes just drops that layer (rigged → unrigged). A GPU stage with no worker (schedule-to-start timeout) or no
backend (`StageUnavailable`) falls back at once. A broken assembled scene falls back to all-procedural. Meshes never
fail a job. Every decision (routes tried, why, attribution, STEP download for CAD) is in `JobOutcome.scene_report`
(web: "3D assets" tab). Finished assets are cached across jobs under `assets/<hash>/`.

Physics stays authoritative: articulated entities rotate about their spec joint from the solver's angle tracks; ML
skeletons, library rigs and CAD joints are secondary motion only (root translation zeroed; a clip's `drive_state`
scales its rate). CAD joints animate mechanisms inside one entity (wheels, propellers); an entity's swing still comes
from its SimSpec joint, because every route's mesh is re-centred and scaled to the replaced primitive's size.
The scene contract is in `docs/playback-format.md` ("scene.gltf v3 extras"). The GPU worker
(`agents/mesh_gpu`) runs one activity at a time. Its image is CUDA 12.8 / PyTorch 2.7 (Blackwell `sm_120` works);
upstream TRELLIS.2 wants ≥ 24 GB (`MESH_GEN_PIPELINE=1024_cascade`, the GKE `gpu` pool), and 12 GB cards use `512`.
`MESH_*_BACKEND=fake` swaps the models for CPU stand-ins so the whole path runs (and is tested) without a GPU.
