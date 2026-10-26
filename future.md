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
- **Why deferred:** TRELLIS.2 needs Linux and a >= 24 GB NVIDIA GPU (verified upstream on A100/H100); the dev laptop
  has 12 GB. Everything else is tested with `MESH_*_BACKEND=fake` stand-ins, so the model calls themselves
  (`runners/trellis_generate.py`, the UniRig launch scripts) have never executed.
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

## Multimodal input: video
- **What:** Video clips as simulation prompts. Sketches, photos and voice notes are done (the intake agent,
  2026-10-26).
- **Why deferred:** Frame extraction needs ffmpeg in the intake image, plus a policy for which frames to send to vision.
- **Approach:** Extract a handful of keyframes (scene-change detection) and the audio track, then reuse the intake's
  image description and transcription.

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
