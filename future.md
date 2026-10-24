# Future work (out of MVP scope)

Everything here was consciously deferred. Each item: **what**, **why deferred**, **prerequisites**, **approach**.
When a request falls outside the MVP milestones in `docs/architecture.md`, add it here instead of building it.

## Game engine export
- **What:** Export approved simulations as engine projects: Godot first, then Summer Engine, Unity, Unreal.
- **Why deferred:** The browser viewer covers MVP playback; engine export doubles the rendering surface area.
- **Prerequisites:** Stable SimResult and glTF scene format; `EngineExporter` interface.
- **Approach:** Headless Godot 4 build job that imports glTF and plays SimResult keyframes through a GDScript
  player. Investigate how scriptable Summer Engine is (CLI/API, licensing) before committing to an adapter.

## Mesh agent v2: generated meshes, rigs and animation
- **What:** Text- or image-to-3D generation, auto-rigging and bone structures, short skeletal animations.
- **Why deferred:** GPU-heavy, slow, and the hardest part to make reliably high quality.
- **Prerequisites:** GPU node pool; the asset-library Mesh agent from the MVP.
- **Approach:** Generation model on GPU pods → retopology/decimation → auto-rig → bake animation into glTF.
  Add a mesh-quality evaluator (manifold, polygon budget, UVs, weight-painting sanity).

## Multimodal input
- **What:** Images, video, audio and sketches as simulation prompts.
- **Why deferred:** Text covers the core loop; multimodal input mostly changes the front of the pipeline.
- **Prerequisites:** Stable Researcher/Modeler prompts.
- **Approach:** OpenAI vision for images and frames, OpenAI speech-to-text for audio, then the same text pipeline.

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
