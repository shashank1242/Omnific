# Future work (out of MVP scope)

Everything here was consciously deferred. Each item: **what**, **why deferred**, **prerequisites**, **approach**.
When a request falls outside the MVP milestones in `docs/architecture.md`, add it here instead of building it.

## Game engine export
- **What:** Export approved simulations as engine projects: Godot first, then Summer Engine, Unity, Unreal.
- **Why deferred:** The browser viewer covers MVP playback; engine export doubles the rendering surface area.
- **Prerequisites:** Stable SimResult and glTF scene format; `EngineExporter` interface.
- **Approach:** Headless Godot 4 build job that imports glTF and plays SimResult keyframes through a GDScript
  player. Investigate how scriptable Summer Engine is (CLI/API, licensing) before committing to an adapter.

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
  - **More model libraries:** Smithsonian 3D (CC0) and NASA 3D resources. Sketchfab (one personal token) and Poly
    Haven ship since 2026-11-25; Sketchfab for public deployments still needs per-user sign-in (below).
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

## Environments: scenery assets
- **What:** Environment props and terrain from the mesh router (library/procedural/generated, vision-checked), an HDRI
  sky, and the environment's look in a "does this read as {environment}?" critic. Environments themselves (chosen in
  clarification, physics through parameters, drawn as sky/ground/fog/lighting) shipped 2026-11-24.

## Follow-up versions: what's left
- **What:** Versions shipped 2026-11-25 (`docs/architecture.md`). Not yet: a side-by-side 3D compare of two versions
  (today the charts overlay the previous version), restoring an old version as live without accepting a new one, and
  reusing untouched entities' meshes exactly. A visual edit re-plans every entity today, and only the mesh cache keeps
  unchanged plans cheap.
- **Mesh v2 on a cloud GPU:** TRELLIS.2, RMBG-2.0 and UniRig are verified on a local RTX 5070 Ti (2026-11-24/25). The
  GKE `gpu` pool (L4) is still unvalidated.

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

## Scene dressing: what's left
- **What:**
  - **Large results on GKE:** the k8s runner returns results through the pod log (6 MiB cap), so a dressed scene or
    .blend with big library textures fails there (the job keeps SceneBuild's scene). Upload from the runner with a
    short-lived signed URL instead.
  - **GPU stills / previews:** the critic's stills render with Cycles on the CPU; EEVEE or GPU Cycles would allow
    more and larger frames.
  - **Sky-map lighting:** a CC0 HDRI (Poly Haven) for the .blend and an environment map in the viewer.
  - **The asset picker on follow-up versions:** a visual edit ("make the car a vintage racer") could offer a fresh
    set of models; today versions use the router's choice.
  - **Re-dressing on follow-up versions:** a visual edit re-plans meshes and re-dresses from scratch; reusing the
    previous dressing's scenery when the setting is unchanged would be faster and steadier.
- **Why deferred:** the MVP keeps one result path, CPU-only runners and full re-runs.

