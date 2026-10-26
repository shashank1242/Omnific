# Future work (out of MVP scope)

Everything here was consciously deferred. Each item: **what**, **why deferred**, **prerequisites**, **approach**.
When a request falls outside the MVP milestones in `docs/architecture.md`, add it here instead of building it.

## Game engine export
- **What:** Export approved simulations as engine projects: Godot first, then Summer Engine, Unity, Unreal.
- **Why deferred:** The browser viewer covers MVP playback; engine export doubles the rendering surface area.
- **Prerequisites:** Stable SimResult and glTF scene format; `EngineExporter` interface.
- **Approach:** Headless Godot 4 build job that imports glTF and plays SimResult keyframes through a GDScript
  player. Investigate how scriptable Summer Engine is (CLI/API, licensing) before committing to an adapter.

## Mesh v2 on real GPUs (validation)
- **What:** Run the `mesh-gpu` worker (TRELLIS.2 + UniRig) on real hardware for the first time: build the image, run
  generated-backend jobs, tune `poly_budget`, timeouts and evaluator thresholds against real model output.
- **Why deferred:** Upstream TRELLIS.2 is verified on ≥ 24 GB GPUs (A100/H100). The image is now CUDA 12.8 / PyTorch
  2.7, and on the dev laptop's RTX 5070 Ti (12 GB, `sm_120`) both stacks load and run CUDA (2026-10-27): TRELLIS.2
  (flash-attn, FlexGEMM, CuMesh, o-voxel, nvdiffrast) and UniRig (bpy, torch_scatter/cluster, a spconv sparse conv on
  the GPU). Still unverified: a full generation at `MESH_GEN_PIPELINE=512` (first run downloads the weights) and a
  UniRig rig. The model calls themselves (`runners/trellis_generate.py`, the UniRig launch scripts) have never
  produced an asset.
- **Prerequisites:** a GPU: the GKE `gpu` pool (`enable_gpu_pool`, L4 24 GB, at TRELLIS.2's floor) or a rented
  A100/H100 Linux VM. Billable: needs explicit approval.
- **Approach (rented VM, cheapest):** docker build `agents/mesh_gpu/Dockerfile` there; run it with `--gpus all`,
  `TEMPORAL_ADDRESS` / artifact store pointing at a reachable stack, `MESH_GEN_BACKEND=trellis2`,
  `MESH_RIG_BACKEND=unirig`; submit jobs with `mesh_backend=generated`. **GKE:** `terraform apply -var
  enable_gpu_pool=true`, push the `mesh-gpu` image, `helm upgrade --set services.mesh-gpu.enabled=true`, set
  `MESH_BACKEND=generated` on the API. Watch the "3D assets" tab: fallbacks and failed checks show what to tune.

## Mesh v3: ML animation and mesh benchmarks
- **What:** A text-to-motion model for arbitrary skeletons behind the `Animator` interface (mesh v2, 2026-10-26, ships
  generation + UniRig rigging + procedural clips); a benchmark set of prompts with reference meshes to track
  generation, rig and clip quality over model versions.
- **Why deferred:** No candidate passed the licence/automation bar. UniMate (best fit: text → motion for any rig)
  has CC BY-NC 4.0 weights and an interactive joint-labelling step; AnyTop (MIT) needs existing motion data per
  skeleton; Puppeteer's animation is video-driven. Puppeteer (Apache-2.0) remains an alternative rigger.
- **Approach:** Re-check UniMate-style releases; wire one as `MESH_ANIM_BACKEND`; the motion evaluators and the vision
  critic already exist.

## Mesh router: more libraries and richer CAD
- **What:**
  - **More model libraries:** Sketchfab (the largest CC catalogue), Smithsonian 3D (CC0) and NASA 3D resources.
  - **CAD closed loops:** four-bar linkages and other mechanisms the tree-shaped joint graph can't express.
  - **Animated sliders:** pistons and other slider joints. Clips are rotation-only today, so sliders don't animate.
  - **Physical scale:** CAD models kept at true size, with their joints matched to SimSpec pivots, instead of being
    rescaled to the replaced primitive's size like every other route.
- **Why deferred:**
  - Sketchfab's Download API requires every end user to sign in with their own Sketchfab account (OAuth), which a
    server-side pipeline can't do without a separate agreement with them.
  - Closed loops need a constraint solver.
  - True-scale assets need the scene to use physical units end to end.
- **Approach:**
  - **Sketchfab:** a per-user OAuth flow in the web app, with the token passed to the library route for that job only.
  - **Closed loops:** a `couple`/loop-closure vocabulary in `omnific_cad` plus a small planar solver in the harness.
  - **Sliders:** bone translation channels in the clip format, plus checks that each joint stays inside its limits.

## Multimodal input: video
- **What:** Video clips as simulation prompts. Sketches, photos and voice notes are done (the intake agent,
  2026-10-26).
- **Why deferred:** Frame extraction needs ffmpeg in the intake image, plus a policy for which frames to send to vision.
- **Approach:** Extract a handful of keyframes (scene-change detection) and the audio track, then reuse the intake's
  image description and transcription.

## Simulation environments (chosen during clarification)
- **What:** Every simulation gets a surrounding environment instead of empty space around the entities: a lab bench,
  a racetrack, a park, open sea, low Earth orbit and so on. The environment is chosen in the clarification phase. The
  clarifier always asks an "environment" question, with options suggested from the prompt (plus a custom answer; see
  the next item). The answer then flows into the SimSpec as an `environment` block: setting, ground or sky,
  lighting, scale, and physically meaningful properties such as gravity, air density, ground friction and
  water/fluid. The modeler must use those properties, so a "Moon" environment really changes g.
- **Why deferred:** It touches clarify (a new mandatory question), contracts (`SimSpec.environment`), the modeler and
  evaluator (environment parameters become cited facts or defaults), the mesh router (environment assets) and the
  viewer (sky/lighting/ground). That's more than the MVP's "simulation in empty space" scope.
- **Prerequisites:** The mesh router and library route (2026-10-27), which can source environment props and ground
  meshes. Viewer support for an environment layer (sky box or HDRI, ground material, fog) that doesn't interfere
  with entity tracks.
- **Approach:**
  - **Clarifier:** emits an `environment` question (kind `choice`, 3-5 prompt-specific options, default = the most
    plausible setting); skipped when the prompt already names the setting.
  - **SimSpec:** gains `environment: {name, description, gravity, fluid, ground, lighting, scale_m}` (strict-schema
    checked). Physical values follow the clarification precedence (user answer > documents > default).
  - **SceneBuild:** a new per-scene "environment" plan item. It is routed like entities: library (props, terrain),
    procedural (ground plane, sky gradient) or generated. Each item is evaluated (vision critic: "does this read as
    {environment}? does it hide the entities?") and falls back to the current empty scene.
  - **scene.gltf:** an `environment` node plus `extras.omnific.environment` (sky colour or HDRI, fog, light rig). The
    viewer renders it under the entities and keeps the camera framing on the entities.
  - **Evaluator:** a check that the spec's environment parameters were actually used (e.g. g matches the chosen body).

## Custom answers for every clarifying question
- **What:** Every clarifying question accepts a free-form custom answer, including choice and yes/no questions,
  which today only accept their listed options (`ClarifyQuestion.kind` in `contracts.py`, the option buttons in
  `apps/web/components/ClarifyForm.tsx`).
- **Why deferred:** Requested alongside the environment feature; it changes the answer contract and needs validation
  of free text where the agents currently get a known option.
- **Approach:**
  - **Web:** a "Custom…" option on every choice and boolean question, revealing a text field. Number questions
    accept a value with a unit, e.g. "3 ft".
  - **API / workflow:** `ClarifyAnswer` gains `custom: bool`.
  - **Clarification check:** a custom answer goes through a small `MODEL_FAST` normalisation step. It maps the text
    onto the question: a number becomes a value in the question's pint unit, a choice becomes the nearest option or
    stays free text. The result is recorded in `Clarification`, with the user's original words kept for citations
    (`omnific://input/clarifications`). Text it can't interpret is sent back to the user as a follow-up question,
    not silently replaced by the default.
  - **Precedence:** custom answers count as explicit user answers (above documents and defaults).

## Edit a finished simulation (follow-up changes)
- **What:** After a simulation is built (awaiting review or approved), the user can ask for changes in plain
  language, optionally with new attachments, instead of starting over. Examples: "make the ramp 30° and add
  friction", "swap the ball for a bowling ball", "add a second pendulum", "run it for 20 s", "make the car red", "put
  it on the Moon". Each edit produces a new **version** of the job. Earlier versions stay viewable and can be
  compared or restored.
- **Why deferred:** Today the only way to change a built simulation is a review reject, which re-runs the whole
  revision loop from the modeler. A proper edit needs change classification, partial re-runs, versioned artifacts
  and a UI for history, all beyond the MVP's one-shot flow.
- **Prerequisites:** Versioned artifact keys (`jobs/<id>/v<n>/...`); the mesh cache (already keyed by plan, so
  untouched entities are reused for free); the environment and custom-answer items above if edits may change them.
- **Approach:**
  - **API/UI:** `POST /jobs/{id}/edits {request, inputs?}` and an "Edit" box under the viewer. The request goes
    through intake like a prompt (sketches, voice notes).
  - **Workflow:** an `edit` signal on `SimulationJob`, or a child `SimulationEdit` workflow, starts version n+1 from
    version n's SimSpec, facts, clarifications and assets.
  - **Change classifier** (`MODEL_GENERAL`, structured output): sorts the request into a minimal re-run plan. It
    re-runs only the stages the change touches.
    - **Visual-only** (colours, mesh swap, environment look): SceneBuild for the affected entities only.
    - **Parameter change** (values, duration, initial conditions): patch the SimSpec, re-run the solver and the
      evaluator. Solver code is reused if it reads parameters from the spec.
    - **Structural change** (new or removed entities, new physics): the modeler revises the spec with a diff-style
      prompt, then the solver-coder, sandbox and evaluator run.
    - **Ambiguous** requests become clarifying questions (with custom answers).
  - **Checks on every edit:**
    - The evaluator runs on each new version.
    - A diff check confirms the change actually happened and nothing unrequested changed: SimSpec diff vs the
      request, and unchanged entities keep identical tracks when only visuals changed.
    - The critic sees the request and both versions.
    - The human review applies to the new version; the previous approved version stays live until it's approved.
  - **Viewer:** a version picker and a side-by-side or overlay compare (playback v1 vs v2), with the change list
    shown in the trust panel.

## Real-time and interactive simulations
- **What:** Live parameter tweaking, streamed solver output, multi-user sessions.
- **Why deferred:** The MVP is batch: solve, evaluate, review, play.
- **Prerequisites:** Solvers that can be stepped incrementally; WebSocket channel in the API.
- **Approach:** Steppable solver interface, server-side session pods, delta streaming to the viewer.

## Wider domains and heavier solvers
- **What:** CFD (OpenFOAM), molecular dynamics (OpenMM, LAMMPS), quantum chemistry, N-body astrophysics,
  FEM (FEniCSx), rigid-body robotics (MuJoCo), GPU solvers (JAX, NVIDIA Warp).
- **Why deferred:** Big images, long runtimes, specialised validation.
- **Prerequisites:** Job queue with per-solver images and resource classes.
- **Approach:** One solver image per family, selected by SimSpec domain, each with its own benchmark suite.

## Stronger evaluation
- **What:** Benchmarks against experimental and published data, uncertainty quantification and sensitivity
  analysis, adversarial red-team prompts, evaluator score dashboards.
- **Why deferred:** The MVP evaluators (units, conservation, convergence, bounds, analytic spot check, LLM critic)
  cover the basics.
- **Approach:** Versioned benchmark corpus; Monte Carlo over parameter uncertainty; evals run in CI.

## Human in the loop at scale
- **What:** Reviewer queues, roles, domain-expert routing, learning from approve/reject feedback.
- **Why deferred:** The MVP has a single review step.
- **Approach:** Review service with assignment rules; store feedback as eval data and few-shot examples.

## Platform
- **What:** Auth, multi-tenancy, billing and quotas, sharing and a sim marketplace, versioning and forking,
  a public API/SDK.
- **Why deferred:** Not needed to prove the core loop.

## Infrastructure
- **What:** Multi-region, GPU autoscaling (KEDA, Karpenter / cluster autoscaler), cost controls, service mesh
  with mTLS, OpenTelemetry tracing across agents, disaster recovery.
- **Why deferred:** The MVP runs on a single cluster with basic node pools.

## Model flexibility
- **What:** A provider-agnostic LLM layer, fine-tuned or self-hosted models for cheap stages, prompt and eval
  regression tests.
- **Why deferred:** The MVP is OpenAI-only, behind a thin client wrapper so that swapping providers later stays cheap.


## Articulation v2: joints beyond planar rotation
- **What:** 3-D joints (ball and hinge on any axis), prismatic joints and rigid-body orientation (quaternions), for
  robotics, spinning tops and 3-D orbits.
- **Why deferred:** Playback v2 (2026-10-24) covers planar rotation about z with parent chains: pendulums, linkages
  and 2-D orbits.

## More clouds
- **What:** Terraform for EKS (Kata/gVisor via a custom AMI or Firecracker) and AKS (Kata containers), next to GKE.
- **Why deferred:** The MVP targets GKE only, because GKE Sandbox gives managed gVisor node pools.

## MCP tools on GKE
- **What:** Run the Docker MCP gateway (or individual MCP servers) inside the cluster so the researcher gets the same MCP
  tools in production.
- **Why deferred:** The Docker MCP gateway starts each MCP server as a container through the Docker socket, which GKE nodes
  don't expose (and shouldn't). Locally it works through docker-compose.
- **Approach:** Deploy chosen MCP servers as their own Deployments speaking streamable HTTP (or the gateway in `--static`
  mode pointing at them), with egress allowlists, and set `MCP_GATEWAY_URL` on the researcher. `MCP_GATEWAY_URL` stays
  unset on GKE until then, and the researcher runs without MCP tools.
