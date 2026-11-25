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
 6b. Dress        → SceneDress child workflow (below): Blender restyles, decorates and lights the scene (never its
                    motion), evaluated; the dressed scene.gltf for the viewer plus the run as a .blend
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

## Follow-up versions: change a finished simulation in plain words
An accepted simulation can be changed ("make the ramp 30°", "run it for 20 s", "make the car red", "put it on the
Moon"), optionally with sketches or voice notes. Each change builds a **version**, which is a job of its own: its own
`SimulationJob` workflow id and its own artifacts under `jobs/<version id>/`. Every job endpoint (status, artifacts,
clarify, Accept / Ask for changes, Tune runs, publish) works on a version unchanged.
- `POST /jobs/{id}/edits {request, input_ids}` needs an accepted base and no other version still building. It copies
  the base outcome and its solver source into `JobRequest.edit` (`EditContext`) and writes the link
  `jobs/<version>/version.json` and the root's index `jobs/<root>/versions.json`. `GET /jobs/{id}/versions` lists them
  from any version; `live` marks the newest accepted one, which stays live until a newer one is accepted.
- In the workflow (`_run_edit`), attachments go through the intake and the **edit classifier** (`classify_edit`, a
  second activity on the modeler worker, `MODEL_GENERAL`, deterministic `sanitize_plan` after it) returns an
  `EditPlan`. Only what the change touches re-runs:
  - **visual**: entity `look`s are patched. The same run and evaluation are kept, and only packaging runs (the mesh
    planner follows `look`).
  - **parameters**: values / initial values / duration are patched (`omnific_common.edits.apply_plan`). The base
    solver runs unchanged (`keep_code`), then the evaluator.
  - **structural**: the modeler revises the previous SimSpec with the request (`previous_spec`, `change_requests`).
  - **unclear**: the classifier's questions are asked like clarifications, then it classifies again. A failing
    classifier, or a plan naming something the spec doesn't have, falls back to structural.
- Checks: the evaluator runs on every version that re-ran the solver. `spec_diff` (deterministic) records what
  actually changed (`JobOutcome.edit.diff`, shown as "What changed"), and a structural change that changed nothing
  gets one explicit retry. Then the usual review.
- The viewer shows a version strip, the change and its diff, and offers the previous version as a compare-only run
  in the Tune tab.

## Evaluators (all run before human review)
Deterministic first: dimensional consistency, finiteness, bounds, conservation drift, convergence (refine=1 vs
refine=2) and analytic spot checks (`SimSpec.analytic_checks`), and geometry (`geometry[...]`: with true entity sizes nothing sinks into the
ground or passes through a ramp; playback-format.md, "True sizes"). Only if those all pass does the LLM critic run
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

## Scene dressing in Blender (SceneDress)
After packaging (playback + SceneBuild), `SceneDress` (`services/workflows/.../dress.py`) makes the scene look good
without touching the simulation. The result stays an interactive 3D scene in the viewer; nothing is rendered to video.

| Stage | Worker | What |
|---|---|---|
| `dress_write` | dress (`MODEL_REASONING`) | a scene brief (setting, each entity's kind, asset, size and the space it moves through, in Blender axes) → three bpy scripts: Look (entity materials through `omnific.restyle` only), Scenery (static set dressing outside the motion), Lighting (sun / point / spot). On a retry: the critique and its previous scripts |
| `run_blender` | sandbox → Blender runner (`sandbox/blender-runner.Dockerfile`: Python 3.11 + `bpy`, no network, non-root) | imports SceneBuild's scene.gltf; keys every entity from playback.json with the viewer's math (`playback_math.py`, pinned to the TypeScript by `apps/web/lib/fixtures/playback-golden.json`); runs each script after an AST guard (`blender_safety.py`); exports only the `scenery` collection and merges it into SceneBuild's JSON (`gltf_merge.py`); saves the run as `scene.blend` (plays on Space: the whole run in 10 s at 30 fps, like the viewer at 1×); renders 4 Cycles CPU stills for the critic |
| `check_scene` | mesh | the scene.gltf contract (`evaluate_scene`) on the dressed file |
| `dress_evaluate` | dress (`MODEL_GENERAL`) | every script ran; **protected set** (entity objects unchanged in Blender) and **entity nodes byte-identical** in the dressed glTF; **clearance** (no scenery inside the space an entity sweeps, except below its lowest point); the scene contract; then a vision critic on the stills (only what dressing controls: materials, scenery, lighting, visibility). `blend.fidelity` (≤ 1e-3 scene units vs the viewer's math) and `blend.framing` (the .blend's default camera keeps every moving object in view) are reported but don't gate |

A failed evaluation goes back to the writer (`max_attempts` 3; a runner that cannot start stops at once). Dressing
never fails a job: without a pass the job keeps SceneBuild's scene and the harness's `export` mode still builds the
.blend. `JobOutcome.scene_key` is the dressed scene when it passed (`raw_scene_key` keeps SceneBuild's),
`blend_key` the .blend, `dress_report` the attempts, checks and scripts ("3D models" tab). The viewer reads
`scenes[0].extras.omnific_scene` (playback-format.md, scene.gltf v4) to scale Blender's photometric lights and dim
its own rig to a fill. "Open in Blender" downloads `scene.blend`; for a Tune run, `POST /jobs/{id}/variants/{vid}/blend`
starts `BlendExport` (export mode, no LLM) and `GET .../scene.blend` returns 202 until the file is ready.

## Asset picker (choose the 3D models while the job builds)
With `mesh_backend=generated` and `JobRequest.pick_assets` (default on), SimulationJob starts the `AssetChoices` child
workflow (`services/workflows/.../choices.py`) the first time the modeler returns a spec, so the search runs next
to coding, running and evaluating and costs the user no time:

1. `plan_assets` (the mesh router) gives each entity its routes and search query.
2. `asset_choices` (mesh worker, one per entity in parallel): every enabled model library is searched (the router's
   query, else the entity's kind), licence-filtered to CC0 / CC-BY, and ranked by the vision shortlist with a 0-10
   score per option (up to 6). Thumbnails are re-encoded as PNG and stored under `jobs/<id>/choices/<entity>/`, so the
   browser never loads third-party images; the API serves them only by the job's own keys.
3. The child signals the job (`choices_update`); `GET /jobs/{id}` shows them (`asset_choices`) until packaging.
4. Where the router would use the library anyway, the top 3 options are fully evaluated with the usual library
   stages (safe download, geometry checks and the vision critic, postprocess), each result signalled so the UI's
   badges update ("Passed every check" / "Failed a check: ..."). Options that pass reach SceneBuild ready to use.

The user picks per object: a type (model library, CAD, AI-generated, simple shape: only the routes this deployment
serves) and, for the library, a specific model (`POST /jobs/{id}/assets/picks` -> `pick_assets` signal). Picks are
accepted until packaging starts. Packaging waits in `awaiting_asset_picks` only if nothing was picked, for
`pick_timeout_minutes` (default 30), then uses the evaluator's choice. `resolve_picks` keeps only picks that match
what was offered (and didn't fail), so a stale or forged pick can't name an arbitrary model. In SceneBuild a picked
model uses its pre-evaluated asset or is evaluated then; if it fails, the entity falls back to its planned routes (noted
in the 3D-models report). A picked type leads that entity's routes; with no pick, an option that already passed is used
directly. Every pick and its outcome is in `JobOutcome.asset_choices` / `asset_picks` and the scene report notes.

## Model libraries and scenery props
The library route and the asset picker search every source in `MESH_LIBRARY_SOURCES`
(`agents/mesh/src/omnific_mesh/library/sources.py`), all licence-filtered to CC0 / CC-BY:

| Source | Search | Download |
|---|---|---|
| `objaverse` | local pgvector index | GLB from Hugging Face |
| `polypizza` | live API (`POLYPIZZA_API_KEY`) | direct GLB |
| `sketchfab` | live public search (licence by label: CC Attribution / CC0 Public Domain only; archives over `LIBRARY_MAX_MB` skipped) | `fetch_glb`: the short-lived GLB link from `/v3/models/{uid}/download` with the personal `SKETCHFAB_API_TOKEN` (sent only to api.sketchfab.com, redirects off), then the safe downloader without any credentials |
| `polyhaven` | the public catalogue (522 models), cached 6 h and ranked locally by name / tags | `fetch_glb`: the 1k glTF and the files the API lists, each through the safe downloader on one shared size budget, packed into one GLB (`library/pack.py`) |

Every model then goes through `check_glb` and our own reader / writer like before.

**Scenery props (scene dressing).** `scenery_props` (mesh worker, `DRESS_PROP_SOURCE=polyhaven`, the default) gives
the dress writer up to 30 Poly Haven models that fit the setting (with their real sizes); the writer lists the ones its
Scenery script places with `omnific.prop(id, location, rotation, size)`. SceneDress fetches those (re-encoded, stored
under `jobs/<id>/props/`, at most 8, reused across attempts) and hands them to the Blender runner, which has no
network. The harness scales a prop from metres to scene units, stands it on its base, keeps it in the scenery (so the
clearance check covers it) and credits it in the dressed scene's `asset.copyright`; `DressReport.props` lists them.

