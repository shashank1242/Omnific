# Omnific web

Next.js 15 (App Router, React 19, TypeScript strict) viewer and job UI. 3D via three + @react-three/fiber + drei, styling via Tailwind 3.

## Run

```bash
cd apps/web
npm install
npm run dev          # http://localhost:3000
```

### Without the API (demo bundles only)

Open `/` and pick a demo, or go to `/demo/<slug>`. Demos are read from `public/demo/index.json` and
`public/demo/<slug>/{outcome.json,playback.json,scene.gltf[,solver.py]}`. The review panel is local-only there.
Creating a job will fail with a connection error until the API is up.

### With the API

Start the FastAPI gateway (`services/api`, default `http://localhost:8000`) and Temporal workers, then:

```bash
OMNIFIC_API_URL=http://localhost:8000 npm run dev
```

`/api/:path*` is proxied to `${OMNIFIC_API_URL:-http://localhost:8000}/:path*` by `app/api/[...path]/route.ts`.
`OMNIFIC_API_URL` is read at request time, so one image works in every environment. Set it when you run the container.

## Scripts

`dev`, `build`, `start`, `lint` (eslint flat config), `typecheck` (`tsc --noEmit`), `test` (vitest; pure logic in `lib/`).

## Layout

- `lib/` pure logic: `playback.ts` (parse/validate, interpolation), `tracks.ts` (channels to entity poses, legends), `colormap.ts` (viridis/inferno), `types.ts` (TS mirror of the Python contracts), `api.ts`.
- `components/` `SimulationView` (shared by job and demo pages), `Viewport` (three), `Transport`, `Charts` (SVG + canvas heatmap), `Panels` (trust, evaluation, code, review), `player.ts` (playback clock).
- `app/` routes: `/`, `/jobs/[id]` (polls every 2 s), `/demo/[slug]`.

## Docker

```bash
docker build -t omnific-web .
docker run -p 3000:3000 -e OMNIFIC_API_URL=http://host.docker.internal:8000 omnific-web
docker run -p 3000:3000 omnific-web
```

Multi-stage, `node:22-alpine`, standalone output, runs as a non-root user.
