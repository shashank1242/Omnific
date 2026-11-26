#!/usr/bin/env bash
# Set up and start the Omnific website (the whole stack in Docker), then open it. macOS / Linux / WSL.
#   ./setup.sh [--dev] [--no-build] [--no-open] [--gpu] [--timeout MINUTES]
# Steps (safe to re-run): check Docker; create .env and ask for the OpenAI key (hidden, saved only to .env); build
# the images (services, web, solver/CAD/Blender runners); start the stack; wait for the API and site; open it.
#   --dev       also set up local development (uv workspace + web dependencies)
#   --no-build  start without rebuilding images
#   --no-open   do not open the browser
#   --gpu       also start the GPU worker (3D generation; NVIDIA GPU with Docker GPU support)
set -euo pipefail
cd "$(dirname "$0")"

SITE_URL="http://localhost:3000"
API_HEALTH="http://localhost:8000/healthz"
DEV=0 BUILD=1 OPEN=1 TIMEOUT=10
PROFILES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --dev) DEV=1 ;;
    --no-build) BUILD=0 ;;
    --no-open) OPEN=0 ;;
    --gpu) PROFILES=(--profile gpu) ;;
    --timeout) shift; TIMEOUT="${1:?--timeout needs minutes}" ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)"; exit 2 ;;
  esac
  shift
done

step() { printf '\n=== %s ===\n' "$1"; }
fail() { printf '\nSetup stopped: %s\nFix it and run ./setup.sh again (it is safe to re-run).\n' "$1" >&2; exit 1; }
compose() { docker compose ${PROFILES[@]+"${PROFILES[@]}"} "$@"; }

env_get() {  # value of KEY in .env ('' if absent)
  sed -n "s/^[[:space:]]*$1[[:space:]]*=\(.*\)$/\1/p" .env | tail -n 1 | sed 's/[[:space:]]*$//'
}
env_set() {  # KEY VALUE: replace the line or append it (the value goes through a file, never the command line)
  local key="$1" tmp
  tmp="$(mktemp)"
  KEY="$key" VALUE="$2" awk 'BEGIN { k = ENVIRON["KEY"]; v = ENVIRON["VALUE"]; done = 0 }
    $0 ~ "^[[:space:]]*" k "[[:space:]]*=" { print k "=" v; done = 1; next } { print }
    END { if (!done) print k "=" v }' .env > "$tmp"
  cat "$tmp" > .env && rm -f "$tmp"
}
read_secret() {  # prompt -> hidden input on stdout
  local v=""
  if [ -t 0 ]; then IFS= read -r -s -p "$1: " v; echo >&2; fi
  printf '%s' "$v"
}
wait_url() {  # url what deadline_epoch
  printf 'Waiting for %s' "$2"
  while [ "$(date +%s)" -lt "$3" ]; do
    if curl -fsS -o /dev/null --max-time 5 "$1" 2>/dev/null; then echo " ready."; return 0; fi
    printf '.'; sleep 3
  done
  echo " not ready."; return 1
}

step "1/5 Docker"
command -v docker >/dev/null || fail "Docker was not found. Install Docker Desktop (or Docker Engine + Compose v2) and re-run."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing. Update Docker."
command -v curl >/dev/null || fail "curl is needed to check that the site is up."
if ! docker info >/dev/null 2>&1; then
  if [ "$(uname)" = "Darwin" ] && [ -d "/Applications/Docker.app" ]; then
    echo "Docker is not running: starting Docker Desktop..."
    open -a Docker
    for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; printf '.'; done; echo
  fi
  docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start it and re-run."
fi
docker --version
docker compose version

step "2/5 Configuration (.env)"
if [ -f .env ]; then echo ".env exists: keeping your settings."
else cp .env.example .env && echo "Created .env from .env.example."; fi
key="$(env_get OPENAI_API_KEY)"
if [ -z "$key" ] || [ "${key#<}" != "$key" ]; then
  echo "Omnific's agents use OpenAI models. Paste your OpenAI API key (typing is hidden; it is saved only in .env)."
  key="$(read_secret "OpenAI API key (Enter to skip)")"
  if [ -n "$key" ]; then env_set OPENAI_API_KEY "$key" && echo "Saved OPENAI_API_KEY to .env."
  else echo "Skipped: the site will open, but new simulations fail until OPENAI_API_KEY is set in .env."; fi
else
  echo "OPENAI_API_KEY is set."
fi
key=""
if [ -z "$(env_get SKETCHFAB_API_TOKEN)" ]; then
  echo "Optional: a personal Sketchfab API token lets Omnific use real 3D models from Sketchfab"
  echo "(sketchfab.com/settings/password). Leave it empty to use the other model libraries."
  token="$(read_secret "Sketchfab token (Enter to skip)")"
  if [ -n "$token" ]; then
    env_set SKETCHFAB_API_TOKEN "$token"
    sources="$(env_get MESH_LIBRARY_SOURCES)"
    case ",$sources," in *,sketchfab,*) ;; *) env_set MESH_LIBRARY_SOURCES "${sources:+$sources,}sketchfab" ;; esac
    echo "Saved SKETCHFAB_API_TOKEN to .env."
  fi
  token=""
fi

if [ ${#PROFILES[@]} -gt 0 ]; then env_set MESH_GPU_ENABLED 1 && echo "GPU worker on: MESH_GPU_ENABLED=1 in .env."; fi

step "3/5 Build the images"
if [ "$BUILD" = 1 ]; then
  echo "The first build downloads and builds everything (Blender, CAD and solver runners included): it can take"
  echo "15-30 minutes. Later builds reuse the cache."
  compose build || fail "docker compose build failed (see the output above)."
else
  echo "Skipped (--no-build)."
fi

step "4/5 Start the stack"
compose up -d || fail "docker compose up failed (see the output above)."
deadline=$(( $(date +%s) + TIMEOUT * 60 ))
if ! { wait_url "$API_HEALTH" "the API" "$deadline" && wait_url "$SITE_URL" "the website" "$deadline"; }; then
  compose ps
  fail "the site did not come up within $TIMEOUT minutes. See 'docker compose logs api web' for why."
fi

step "5/5 Finish"
if [ "$DEV" = 1 ]; then
  command -v uv >/dev/null || fail "uv is needed for --dev: https://docs.astral.sh/uv/getting-started/installation/"
  command -v npm >/dev/null || fail "Node.js (LTS) is needed for --dev."
  uv python install 3.11 && uv sync --all-packages --python 3.11 || fail "uv sync failed."
  (cd apps/web && npm ci) || fail "npm ci failed."
fi
printf '\nOmnific is running.\n  Website      %s\n  Library      %s/explore\n  Temporal UI  http://localhost:8233\n\n' "$SITE_URL" "$SITE_URL"
printf '  Logs:  docker compose logs -f api web workflows\n  Stop:  docker compose down      (your simulations are kept in Docker volumes)\n'
if [ "$OPEN" = 1 ]; then
  if command -v open >/dev/null && [ "$(uname)" = "Darwin" ]; then open "$SITE_URL"
  elif command -v xdg-open >/dev/null; then xdg-open "$SITE_URL" >/dev/null 2>&1 || true
  fi
fi
