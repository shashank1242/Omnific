# Omnific

**Prompt-to-simulation platform.** A user describes any physical event in plain words, such as "a pizza baking on
Venus", "an atomic bomb exploding at the event horizon of a black hole" or "how a vacuum-tube computer adds two
numbers". A pipeline of OpenAI-powered agents researches the physics, writes a mathematical model and the code that
solves it, runs that code in a locked-down sandbox and checks the result against the laws of physics. It builds a 3D
scene with real models, dresses it in Blender, and hands the result to a human reviewer. The user then explores an
interactive simulation in the browser, and can download it as a Blender file.

> **Accuracy comes from the solver layer, not the renderer.** Every result is labelled with its fidelity
> (analytic / validated numerical / heuristic), its assumptions and its cited sources. Anything the user asked for
> that could not be simulated as asked is said plainly on the page.

A visual overview with data-flow and pipeline diagrams is in
[`docs/omnific-architecture.html`](docs/omnific-architecture.html). The detailed design is in
[`docs/architecture.md`](docs/architecture.md).

---

## Contents
1. [The problem and the idea](#1-the-problem-and-the-idea)
2. [What a user can do](#2-what-a-user-can-do)
3. [How it works, end to end](#3-how-it-works-end-to-end)
4. [The agents](#4-the-agents)
5. [The contracts: SimSpec, SimResult and playback](#5-the-contracts-simspec-simresult-and-playback)
6. [Checking the physics: evaluators and human review](#6-checking-the-physics-evaluators-and-human-review)
7. [The 3D scene: models, orientation and Blender dressing](#7-the-3d-scene-models-orientation-and-blender-dressing)
8. [The viewer and the website](#8-the-viewer-and-the-website)
9. [Beyond one run: Tune, Ask, versions, library, demo replay](#9-beyond-one-run-tune-ask-versions-library-demo-replay)
10. [System architecture and technology](#10-system-architecture-and-technology)
11. [Security, safety and honesty](#11-security-safety-and-honesty)
12. [Testing and quality](#12-testing-and-quality)
13. [Run the website](#13-run-the-website)
14. [Configuration](#14-configuration)
15. [Develop](#15-develop)
16. [Repository layout](#16-repository-layout)
17. [Limitations and future work](#17-limitations-and-future-work)

---

## 1. The problem and the idea
Simulation tools are either **accurate but hard to use** (writing equations and solver code by hand) or **easy but
not trustworthy** (game engines and animation tools that only look right). Omnific aims for both:

- **Simulate anything.** There is no fixed library of scenarios. Agents write a model and solver code for each
  request, against a small, tested library of numerical primitives.
- **The solver is the truth.** Everything the user sees (3D motion, effects, charts, key numbers) is played back from
  the solved result. Game-engine physics is never used for accuracy.
- **Checked before shown.** Automated evaluators test every result (units, conservation, convergence, bounds,
  geometry, effects, analytic spot checks), and a human reviewer accepts it or asks for changes.
- **Honest.** Each result states its fidelity, assumptions and sources, what is drawn larger than life ("Not to
  scale"), and what was not simulated as asked.

## 2. What a user can do
- **Describe a scenario** in words, optionally adding sketches, photos, voice notes, and documents (PDF, DOCX, PPTX,
  XLSX, CSV, HTML, Markdown, images) from their document library.
- **Answer a few clarifying questions**, or let Omnific use sensible defaults. Questions can be choices,
  multiple-choice, numbers with units, yes/no or free text, including "Which results do you want to see?".
- **Watch the build.** A live architecture diagram fills with colour as each stage finishes: idea, questions,
  research, model, solver, run, check, 3D scene, ready.
- **Pick the 3D models** for each object while the simulation builds (Sketchfab, Poly Haven, Objaverse, Poly Pizza),
  or let Omnific choose.
- **Explore the result**: orbit, zoom, play and scrub time at any speed, jump between named stages ("Free fall →
  Detonation → Fireball grows → Fading"), drag objects, read key results and charts.
- **Review**: accept the simulation, or ask for changes in plain words.
- **Tune** any value and re-run in seconds, then compare runs side by side.
- **Ask** the built-in guide about the simulation and the physics behind it.
- **Change it later** in plain words ("make the ramp 30°", "put it on the Moon"), which creates a new version.
- **Download** the run as a `.blend` file that plays in Blender when you press Space.
- **Publish** it to the public library (the Explore page), with a 3D thumbnail.

## 3. How it works, end to end
Each simulation is one durable **Temporal workflow**, `SimulationJob`. The browser talks to a FastAPI gateway,
which only starts, queries and signals workflows. All the work happens in agent workers, one per Temporal task
queue, and all generated code runs in sandbox containers.

```
Browser (Next.js + three.js)
   │  /api/* proxy
FastAPI gateway ──► Temporal: SimulationJob ──► agent workers (OpenAI) ──► sandbox runners
   │                         │                          │
   └── outcome snapshots     └── child workflows        └── artifacts in S3 (spec, solver.py, result,
                                 SceneBuild, AssetChoices,  playback.json, scene.gltf, scene.blend)
                                 SceneDress
```

**Stages of a job**

| # | Stage | What happens |
|---|---|---|
| -1 | **Intake** | Only with attachments. Vision describes sketches and photos, speech-to-text transcribes voice notes. Together with the typed prompt they become the *effective prompt* (typed text wins). |
| 0 | **Clarify** | Reads attached documents and asks only what they leave open, such as the setting, key values and which results matter. It waits for answers and uses stated defaults if the user doesn't reply in time. |
| 1 | **Research** | Agentic search of the user's documents (pgvector hybrid search), Docker MCP tools (Wikipedia, arXiv, paper search, fetch) and the web. It returns a summary, the phenomena involved and **cited facts** with units. |
| 2 | **Model** | The modeler writes a **SimSpec**: parameters with sources, state variables, equations, the objects in the scene, visual effects, named stages, key outputs, the environment and an honest list of departures. A validator checks it, and the modeler gets up to 3 repair rounds. |
| 3 | **Code** | The solver-coder writes `solver.py` against `omnific_sim.primitives`. Every value is read from the spec, so the code can be re-run with new values. |
| 4 | **Run** | The sandbox runs the code with no network, as non-root, with a read-only root filesystem and CPU, memory and time limits (gVisor on Kubernetes). It produces a **SimResult**. |
| 5 | **Check** | Deterministic evaluators run first, then an LLM critic using a different model from the writer. A failure is blamed on the modeler or the coder, and the loop goes back to stage 2 or 3, up to a set number of revisions. |
| 6 | **Package** | The **Data** agent turns the result into `playback.json`: tracks, effects, stages and key results. **SceneBuild** makes a 3D asset for every object. **SceneDress** restyles, decorates and lights the scene in Blender and exports a `.blend`. |
| 7 | **Review** | The job waits for a human. **Accept** finishes it; **Ask for changes** sends the words back to the modeler and starts another round. |

Large payloads never travel through Temporal. Activities pass **storage keys** (`jobs/<id>/...` in the S3 artifact
store) instead of bytes. Workflows call activities by name, so the workflow code never imports agent code.

## 4. The agents
Each agent is its own container and Temporal worker (`agents/*`). Model IDs come from environment variables per
role, so they can change without code edits. The critic always uses a different model from the writer.

| Agent | Queue | Role (model role) |
|---|---|---|
| **intake** | `intake` | Describes sketches and photos, transcribes voice notes (`MODEL_GENERAL`, `MODEL_TRANSCRIBE`) |
| **clarifier** | `clarifier` | Decides what to ask: choice, multi, number + unit, yes/no, text (`MODEL_FAST`/`GENERAL`) |
| **librarian** | `librarian` | Ingests documents into pgvector: parsing, chunking, figures read by a vision model, embeddings |
| **researcher** | `researcher` | Tool loop over documents, MCP tools and web search, returning cited facts. Also answers the **Ask** tab (`MODEL_GENERAL`) |
| **modeler** | `modeler` | Writes the SimSpec, plus the edit classifier for follow-up versions (`MODEL_REASONING`) |
| **solver-coder** | `solver-coder` | Writes `solver.py` against the primitives (`MODEL_REASONING`) |
| **sandbox** | `sandbox` | Runs the solver, CAD and Blender code in isolated runner containers |
| **evaluator** | `evaluator` | Deterministic physics checks, then the LLM critic (`MODEL_GENERAL`) |
| **data** | `data` | Builds the playback: tracks, effects with blackbody colours, stages, key results, visibility |
| **mesh** | `mesh` | Plans, finds, builds and checks 3D assets per object, then assembles `scene.gltf` |
| **mesh-gpu** | `mesh-gpu` | Optional GPU route: image → 3D generation, auto-rigging and animation |
| **dress** | `dress` | Writes Blender scripts for look, scenery and lighting, and judges the renders |

All agents call OpenAI only through `omnific_common.llm`, a thin wrapper over the Responses API with **structured
outputs**. Every contract between agents is a Pydantic model that must pass OpenAI's strict JSON schema
(`to_strict_json_schema`).

## 5. The contracts: SimSpec, SimResult and playback
**SimSpec** (`packages/omnific-sim/src/omnific_sim/spec.py`) is the single description of a simulation that every
agent reads and writes.
- `title`, `prompt`, `domains`, `fidelity` (analytic / validated numerical / heuristic), `assumptions`, `citations`
- `parameters` (name, value, pint-parsable unit, source), `state` (variables with initial values), `equations`
  (SymPy-parsable), `invariants`, `bounds`, `analytic_checks`
- `time`: start, end, samples, and the stop rule. *Settle* stops once nothing changes; *fixed* runs to the end.
- `environment`: the setting (lab bench, park, the Moon, deep space…) with its own cited parameters, which must
  reach the equations, so a Moon setting really changes g
- `entities`: the visible objects, with `kind`, `look`, bound states, articulation (`parent`, `pivot`, `arm`),
  true `geometry` (shape, size and place in metres), `faces` (which other object it points at), and
  `appears_at` / `disappears_at`
- `effects`: explosion, flash, shockwave, fire, smoke, glow, accretion disk, jet or splash, drawn from solved states
  (radius, brightness, temperature) so the visuals stay true to the solver
- `phases`: the named stages in time order; `outputs`: the key results (max, min, final, initial, mean, time of max
  or min) with a reason each matters
- `departures`: what the user asked for that is not simulated as asked, shown on the page

**SimResult** holds the solver's time series. **playback.json** (`docs/playback-format.md`) is what the viewer and
the `.blend` play. It contains:
- tracks (position, rotation, scale, colour, colour fields) and articulation
- per-frame effect radius, intensity and colour, with colour from a blackbody fit for temperatures
- stages and key results computed from the full-resolution run
- which objects are visible when
- how much small things were enlarged so they can be seen
- the stop reason and the environment

## 6. Checking the physics: evaluators and human review
The deterministic checks run first:
- **dimensional consistency** of every equation
- **finiteness**
- **bounds**
- **conservation drift**
- **convergence** (the same run at refine 1 and 2)
- **analytic spot checks**
- **environment reach**: setting parameters must appear in the equations
- **effects**: radius and brightness must be on at the same time, so an explosion really shows
- **geometry**: nothing sinks into the ground or passes through a ramp
- **scene spread**: one far-away object must not shrink the rest to specks
- **bindings**: a fixed object must not be moved by its own size

Only when all of these pass does the **LLM critic** read the spec, code and result. Major issues fail the report;
minor ones are shown for information. Each failure carries a *blame* (modeler or coder), so the loop revises the
right stage.

The job then pauses at **human review** (a Temporal signal). The creator accepts it, or asks for changes in plain
words, which feed back to the modeler for another round.

## 7. The 3D scene: models, orientation and Blender dressing
**SceneBuild** (a child workflow) gives every object an asset by trying routes in order, judging each stage:

1. **Library**: openly licensed models (CC0 or CC-BY only) from **Sketchfab** (personal token), **Poly Haven**,
   **Objaverse** and **Poly Pizza**. Downloads are safe (HTTPS, public hosts, size cap), the GLB is re-encoded, and a
   **vision shortlist** picks the model that is the right kind of object.
2. **CAD**: an LLM writes `build123d` code, run in the sandbox CAD runner. It produces parts, joints and a STEP
   file. Containers such as racks and cases are built as open frames, so the working parts show.
3. **Generated**: on a GPU, an image is turned into a 3D model, then auto-rigged and animated.
4. **Procedural**: a simple shape, always available as the fallback.

Every asset passes **deterministic mesh checks** and a **vision critic** on a 4-view contact sheet. It is then
post-processed: degenerate faces dropped, winding fixed, decimated to a triangle budget, textures capped.

- **Which way things face.** A reused or generated model arrives in whatever orientation its author left it. A
  vision check of three labelled axis views finds its **front** and **top** and turns it to a standard pose (front
  along +X, top along +Y). An object with `faces` (a rifle facing its target, a lamp facing its subject) is aimed at
  that object with a turn and a tilt.
- **True sizes.** Objects with geometry are drawn at their real size and place on the same scale as the motion.
  Something too small to see is enlarged by a stated factor, and the viewer says "Not to scale".
- **Model picker** (`AssetChoices`). While the solver works, up to 6 candidates per object are found, checked and
  shown with thumbnails, licence and score. The user picks one, or Omnific picks.

**SceneDress** (Blender, `bpy` 5.0.1) then writes **Look** (materials), **Scenery** (ground, set dressing, CC0 props)
and **Lighting** scripts. They run in the Blender sandbox under an AST guard and a *protected-set* rule: scripts may
restyle objects but never move, rescale, rename or delete them.
- A judge checks the result: every script ran, the protected set is unchanged, the scene contract holds, scenery
  stays clear of the volume the motion sweeps through, the `.blend` motion matches the viewer's math, and the camera
  keeps the action in frame. A vision critic then looks at still renders.
- The `.blend` export keys the whole run, including the effects (glowing meshes and lights), objects appearing and
  disappearing, and timeline markers for the stages.
- Dressing never fails a job. If it doesn't pass, the undressed scene is kept, and the job still gets a `.blend`.

## 8. The viewer and the website
The web app (`apps/web`) uses Next.js 15, react-three-fiber/drei, Tailwind and the "Strobe Study" design system:
dark studio and light contact-sheet themes, built to be accessible and to work at phone width.

- **Viewport**: loads `scene.gltf` and drives every object from playback tracks. It draws shader **effects**
  (fireballs, flashes, shock fronts, smoke, glows, accretion disks, jets, splashes) on simulation time, shows and
  hides objects, and frames the camera on the action. It cuts away (x-ray) a big object when the action happens
  inside it, can strobe moving objects into ghost trails, and lets you drag objects.
- **Transport**: play, pause, scrub and a custom speed, plus a **stage strip** that jumps between named phases.
- **Panels**:
  - **Charts**: steady axes, with other runs dashed for comparison
  - **Ask**: the guide
  - **Tune**: change values and re-run
  - **Trust**: fidelity, assumptions, sources and clarifications
  - **3D models**: the scene report, credits and STEP downloads
  - **Code**: the solver source
- **Key results** cards jump to the moment each value occurs.
- **Notices**: "Not simulated as asked", "Not to scale" and the stop reason.
- **Explore**: the public library, with searchable cards and captured 3D thumbnails.

## 9. Beyond one run: Tune, Ask, versions, library, demo replay
- **Tune (`SimulationVariant`)**: change any parameter, initial value or the end time and re-run the job's own
  solver in the sandbox. There is no LLM, so it takes seconds. Compare runs: charts are overlaid, and a table shows
  how each quantity's end and peak values moved.
- **Ask (`SimulationChat`)**: a guide on every simulation page. Each question goes with the job's own record (spec,
  research, checks, key results, stages, what is enlarged, and the state values at the moment on screen) to the
  researcher worker.
  - It answers questions about the run, its numbers and how far to trust it, and about general physics.
  - It suggests follow-up questions, and says plainly what was not simulated.
- **Follow-up versions**: describe a change in words, and the **edit classifier** decides what it touches. Only that
  part re-runs:
  - visual: only packaging
  - parameters: the same solver with new values
  - structural: the modeler revises the spec
  - unclear: Omnific asks questions first

  Each version is its own job, with a version strip and a "What changed" diff.
- **Public library**: publish an accepted simulation with a thumbnail of the current 3D view, then search it on the
  Explore page.
- **Demo replay** (`DEMO_REPLAY`, on by default): when a prompt matches a published simulation's prompt (ignoring
  case, spacing and punctuation), the site plays the build screen for about 35 s and then opens that finished
  simulation. No AI is called, which is useful for demos without API credits.
- **Snapshots and caching**: a finished job's outcome is saved to the store, so it outlives its Temporal history.
  Finished artifacts are cached as immutable; drafts are revalidated with an ETag.

## 10. System architecture and technology
| Layer | Technology | Where |
|---|---|---|
| Presentation | Next.js 15, React, react-three-fiber/drei, three.js, Tailwind, Playwright | `apps/web` |
| Gateway | FastAPI, Uvicorn | `services/api` |
| Orchestration | Temporal (Python SDK), durable SQLite history on a volume | `services/workflows` |
| Agents | Python 3.11, OpenAI Responses API (structured outputs, tools), Pydantic | `agents/*` |
| Numerics | NumPy, SciPy, SymPy, pint (units) | `packages/omnific-sim` |
| Execution | Docker runners (gVisor/k8s in cloud); build123d for CAD; Blender `bpy` 5.0.1 | `sandbox/` |
| 3D assets | trimesh, fast-simplification, Pillow renders; glTF 2.0 | `agents/mesh` |
| Documents | Postgres + pgvector, hybrid search; Docker MCP gateway tools | `packages/omnific-rag`, `agents/librarian` |
| Storage | S3-compatible artifact store (RustFS locally; GCS/S3 in cloud) | `omnific_common.storage` |
| Deployment | Docker Compose; Helm + Terraform for GKE (EKS/AKS planned) | `docker-compose.yml`, `deploy/` |

**Workflows**:
- `SimulationJob`: the main job
- `SceneBuild`, `AssetChoicesWorkflow`, `SceneDress`: its child workflows
- `BlendExport`: the `.blend` for a Tune run
- `SimulationVariant`: Tune re-runs
- `SimulationChat`: the Ask tab
- `DocumentIngest`: adding documents to the library

## 11. Security, safety and honesty
- **Generated code runs only in sandboxes**: no network, non-root, read-only root, resource and time limits, and
  gVisor on Kubernetes. Blender scripts also pass an AST guard.
- **Strict contracts**: every agent output is schema-validated, and validators reject inconsistent specs. Examples
  are unknown states, effects that never show, abstract "entities" and fixed objects bound to lengths.
- **Licences**: only CC0 and CC-BY models are used, and their credit lines are written into the scene.
- **Secrets**: they live in `.env` (gitignored), and `.env.example` holds only placeholders. The Sketchfab token is
  sent only to `api.sketchfab.com`. The setup scripts take keys as hidden input and never print them.
- **Content safety**: weapons and explosions are modelled and discussed only as impact, blast and energy physics,
  never how to build, modify or use them. When the model declines part of a request, the page says so through
  `departures` instead of hiding it.
- **Prompt-injection hygiene**: documents, web pages and the chat record are passed to models as data, never as
  instructions.
- **Honesty in the UI**: fidelity badges, cited sources, "Not to scale", "Not simulated as asked", the stop reason,
  and a note on Tune runs that they are not re-evaluated.

## 12. Testing and quality
- **Python**: about 630 tests with pytest. They cover contracts, solver primitives, every agent with fake LLMs,
  workflows on the Temporal test server, the API with fakes, mesh and orientation math, and Blender harness tests
  (with `bpy`).
- **Web**: 120+ unit tests with vitest (playback, tracks, effects, framing, chat, replay…), plus **Playwright**
  end-to-end tests on desktop and phone with real fixtures (stages, effects, chat, replay, picker, dressed scenes).
  An opt-in `produce.spec.ts` drives the real site end to end.
- **Static checks**: `ruff`, TypeScript `tsc`, ESLint, `helm lint`. Every commit is reviewed with **CodeRabbit**.
- **Golden fixture**: one fixture is shared by vitest and pytest, so the browser and the `.blend` export compute
  identical motion.

## 13. Run the website
One script sets up and starts the whole site in Docker, then opens http://localhost:3000. It needs Docker Desktop (or
Docker Engine with Compose v2), asks for your OpenAI API key (and optionally a Sketchfab token) the first time, and is
safe to re-run:
```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1    # Windows; -NoBuild, -NoOpen, -Dev, -Gpu
```
```bash
./setup.sh                                             # macOS / Linux / WSL; --no-build, --no-open, --dev, --gpu
```
The first build takes 15 to 30 minutes, because it builds the Blender, CAD and solver runners. Stop the site with
`docker compose down`; simulations are kept in Docker volumes.

| Service | URL |
|---|---|
| Website | http://localhost:3000 (library: `/explore`) |
| API | http://localhost:8000 (`/healthz`) |
| Temporal UI | http://localhost:8233 |
| S3 console | http://localhost:9001 |

## 14. Configuration
All settings live in `.env`; `.env.example` documents every one of them. The main ones:

| Setting | Purpose |
|---|---|
| `OPENAI_API_KEY`, `MODEL_REASONING`, `MODEL_GENERAL`, `MODEL_FAST`, `MODEL_TRANSCRIBE`, `MODEL_IMAGE` | AI provider and model per role |
| `MESH_BACKEND` | `generated` (real models, picker) or `procedural` (simple shapes) |
| `MESH_LIBRARY_SOURCES`, `SKETCHFAB_API_TOKEN` | Model libraries to search (`sketchfab,polyhaven,…`) |
| `MESH_CAD_ENABLED`, `MESH_GPU_ENABLED` | CAD route; GPU 3D generation route |
| `DRESS_ENABLED` | Blender scene dressing and `.blend` export |
| `DEMO_REPLAY` | Known published prompts replay with no AI calls (default `1`) |
| `ARTIFACT_STORE`, `S3_*` / `GCS_BUCKET` | Where artifacts are stored |
| `RAG_DATABASE_URL`, `MODEL_EMBEDDING` | Document library and public library database |
| `MCP_SERVERS`, `MCP_GATEWAY_URL` | Research tools exposed to the researcher |

## 15. Develop
Windows, from `cmd` in the repo root (installs uv if missing; needs Node.js LTS, Docker optional):
```bat
setup.bat              rem Python 3.11 workspace + web deps + .env from .env.example
setup.bat --db         rem also start Postgres (pgvector) for the document library
setup.bat --check      rem also run ruff, pytest and the web checks
```
Then set `OPENAI_API_KEY` in `.env`. Elsewhere: `uv sync --all-packages`, `cd apps/web && npm ci`,
`cp .env.example .env`. Commands for tests, end-to-end runs and the stack are in `CLAUDE.md`. Useful ones:
```bash
uv run --no-sync ruff check . && uv run --no-sync pytest -q          # Python lint + tests
cd apps/web && npm run typecheck && npm run lint && npm test         # web checks
uv run --no-sync python scripts/e2e_local.py "a projectile on Mars"  # whole pipeline in-process, real OpenAI
uv run --no-sync python scripts/e2e_local.py --serve                 # same, with the API on :8000 for the web UI
```

### Blender, model libraries and browser tests
- Scene dressing needs the Blender runner: `docker compose build blender-runner-build` (compose), or for
  `scripts/e2e_local.py` and the harness tests a Python 3.11 with `bpy` in `BLENDER_PYTHON`
  (`uv venv .venv-blender --python 3.11` then `uv pip install --python .venv-blender bpy==5.0.1`).
  `DRESS_ENABLED=0` turns dressing off.
- Model libraries: `MESH_LIBRARY_SOURCES=sketchfab,polyhaven` (Poly Haven needs no key; Sketchfab needs your
  personal `SKETCHFAB_API_TOKEN`, single-user deployments only).
- Browser end-to-end tests (Playwright, drives the installed Edge by default): with the site running,
  `cd apps/web && npm run e2e` (`E2E_BASE_URL`, default http://localhost:3000; `E2E_CHANNEL=chrome` elsewhere).

## 16. Repository layout
| Path | What |
|---|---|
| `packages/omnific-sim/` | SimSpec schema, SimResult, units, solver primitives, visual scale rules, evaluator checks |
| `packages/omnific-common/` | Contracts between agents, the OpenAI wrapper, storage, Temporal helpers |
| `packages/omnific-rag/` | pgvector document store, hybrid search, document tools, the public library table |
| `agents/` | One container per agent: intake, clarifier, librarian, researcher, modeler, solver_coder, data, mesh, mesh_gpu, evaluator, dress |
| `services/api/` | FastAPI gateway: jobs, artifacts, review, clarify, picks, variants, versions, chat, library, replay |
| `services/workflows/` | Temporal workflows: SimulationJob, SceneBuild, AssetChoices, SceneDress, BlendExport, SimulationVariant, SimulationChat, DocumentIngest |
| `sandbox/` | Locked-down runners for generated code: solver, CAD (build123d) and Blender (`bpy`) |
| `apps/web/` | Next.js + three.js viewer, build progress, review, Tune, Ask, library |
| `exporters/` | Browser bundle (game-engine export later) |
| `deploy/` | Helm chart and Terraform for GKE (EKS/AKS planned) |
| `docs/` | Architecture, playback format, RAG, deployment, service conventions, the architecture overview page |
| `scripts/` | Local end-to-end runner, demo generation, Objaverse indexing, CodeRabbit wrapper |
| `setup.ps1`, `setup.sh`, `setup.bat` | One-step website setup (Docker), and the development setup |

## 17. Limitations and future work
- **Fidelity varies**: many scenarios (black holes, oobleck splashes) are *heuristic*, and the page says so.
  Validated numerical results need convergence and analytic checks to pass.
- **Model refusals**: the AI may decline parts of some requests (for example, modelling gunfire). Those parts are
  listed as departures, not faked.
- **Aiming** works between objects that stay in place; following a moving target is future work.
- **Accounts**: there are no user accounts yet, so anyone with the link can publish. The Sketchfab token is per
  deployment (per-user sign-in is planned).
- **Cloud**: the Helm and Terraform files are ready, but nothing is deployed. GPU rendering of stills and videos,
  game-engine export (Godot) and more model sources (Smithsonian, NASA) are listed in `future.md`.
