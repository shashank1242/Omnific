@echo off
rem Omnific one-shot setup for Windows (cmd). Run from the repo root:
rem     setup.bat              Python venv + web deps + .env
rem     setup.bat --db         also start Postgres (pgvector) for the document library
rem     setup.bat --check      also run the test suites and lint afterwards
rem Safe to re-run: every step is idempotent.
setlocal EnableExtensions
cd /d "%~dp0"

set WITH_DB=0
set CHECK=0
for %%A in (%*) do (
  if /I "%%~A"=="--db" set WITH_DB=1
  if /I "%%~A"=="--check" set CHECK=1
  if /I "%%~A"=="--help" goto :usage
  if /I "%%~A"=="-h" goto :usage
)

echo.
echo === 1/5 Prerequisites ===
where uv >nul 2>&1
if errorlevel 1 (
  echo uv not found: installing it with Astral's official installer ^(https://astral.sh/uv^)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" || goto :fail
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  where uv >nul 2>&1 || (echo uv was installed but is not on PATH yet. Open a new cmd window and re-run setup.bat. & goto :fail)
)
uv --version
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js not found. Install the LTS release, then re-run setup.bat:
  echo     winget install OpenJS.NodeJS.LTS
  goto :fail
)
for /f "delims=" %%V in ('node --version') do echo node %%V
where docker >nul 2>&1
if errorlevel 1 (
  echo [optional] Docker not found: needed only for docker compose, the document library and the MCP gateway.
) else (
  docker --version
)

echo.
echo === 2/5 Python workspace (uv, Python 3.11) ===
uv python install 3.11 || goto :fail
uv sync --all-packages --python 3.11 || goto :fail

echo.
echo === 3/5 Web app dependencies ===
pushd apps\web
call npm ci || (popd & goto :fail)
popd

echo.
echo === 4/5 Configuration ===
if exist .env (
  echo .env already exists: left unchanged.
) else (
  copy /Y .env.example .env >nul || goto :fail
  echo Created .env from .env.example. Edit it and set OPENAI_API_KEY before running real jobs.
)
findstr /R /C:"^OPENAI_API_KEY=<" .env >nul 2>&1 && echo WARNING: OPENAI_API_KEY in .env is still the placeholder.

if "%WITH_DB%"=="1" (
  echo.
  echo Starting Postgres ^(pgvector^) for the document library...
  docker compose up -d postgres || goto :fail
)

echo.
echo === 5/5 Checks ===
if "%CHECK%"=="1" (
  uv run --no-sync ruff check . || goto :fail
  uv run --no-sync pytest -q || goto :fail
  pushd apps\web
  call npm run typecheck || (popd & goto :fail)
  call npm run lint || (popd & goto :fail)
  call npm test || (popd & goto :fail)
  popd
) else (
  echo Skipped ^(run "setup.bat --check" to run tests and lint^).
)

echo.
echo Setup complete. Next:
echo   uv run --no-sync python scripts\e2e_local.py "a projectile launched on Mars at 45 degrees"
echo   uv run --no-sync python scripts\e2e_local.py --serve        ^(API on :8000^), then in apps\web: npm run dev
echo   docker compose up --build                                   ^(full stack^)
endlocal
exit /b 0

:usage
echo Usage: setup.bat [--db] [--check]
echo   --db     start Postgres (pgvector) with docker compose for the document library
echo   --check  run ruff, pytest and the web typecheck/lint/tests after installing
endlocal
exit /b 0

:fail
echo.
echo Setup FAILED at the step above. Fix the error and re-run setup.bat (it is safe to re-run).
endlocal
exit /b 1
