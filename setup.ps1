<#
.SYNOPSIS
  Set up and start the Omnific website (the whole stack in Docker), then open it.

.DESCRIPTION
  From the repo root, in PowerShell:
      powershell -ExecutionPolicy Bypass -File .\setup.ps1
  Steps (safe to re-run; each one is idempotent):
    1. Checks Docker (and starts Docker Desktop if it is installed but not running).
    2. Creates .env from .env.example if missing, and asks for your OpenAI API key (and, optionally, a Sketchfab
       token) when .env has none. Keys are typed hidden and written only to .env (gitignored), never printed.
    3. Builds the images: the services, the web app and the three sandbox runners (solver, CAD, Blender).
    4. Starts the stack and waits until the API and the website answer.
    5. Opens http://localhost:3000.

.PARAMETER Dev        Also set up local development (Python workspace with uv, web dependencies): runs setup.bat.
.PARAMETER NoBuild    Start without rebuilding images (faster when nothing changed).
.PARAMETER NoOpen     Do not open the browser at the end.
.PARAMETER Gpu        Also start the GPU worker (3D generation; needs an NVIDIA GPU with Docker GPU support).
.PARAMETER TimeoutMinutes  How long to wait for the site to come up after starting (default 10).
#>
[CmdletBinding()]
param(
  [switch]$Dev,
  [switch]$NoBuild,
  [switch]$NoOpen,
  [switch]$Gpu,
  [int]$TimeoutMinutes = 10
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$SiteUrl = "http://localhost:3000"
$ApiHealth = "http://localhost:8000/healthz"
$Profiles = @()
if ($Gpu) { $Profiles = @("--profile", "gpu") }  # MESH_GPU_ENABLED=1 is written to .env below

function Write-Step([string]$text) { Write-Host ""; Write-Host "=== $text ===" -ForegroundColor Cyan }
function Stop-Setup([string]$text) {
  Write-Host ""
  Write-Host "Setup stopped: $text" -ForegroundColor Red
  Write-Host "Fix it and run setup.ps1 again (it is safe to re-run)."
  exit 1
}

function Test-DockerRunning {
  $ErrorActionPreference = "Continue"  # a native command's stderr must not stop the script (PowerShell 5.1)
  & docker info *> $null
  return ($LASTEXITCODE -eq 0)
}

function Invoke-Compose([string[]]$composeArgs) {
  $ErrorActionPreference = "Continue"
  & docker compose @Profiles @composeArgs
  if ($LASTEXITCODE -ne 0) { Stop-Setup "docker compose $($composeArgs -join ' ') failed (see the output above)." }
}

# --- .env helpers: UTF-8 without a BOM (a BOM would corrupt the first key for docker compose) ----------------------
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Get-EnvValue([string]$path, [string]$name) {
  foreach ($line in [System.IO.File]::ReadAllLines($path, $Utf8)) {
    if ($line -match "^\s*$([regex]::Escape($name))\s*=(.*)$") { return $Matches[1].Trim() }
  }
  return ""
}

function Set-EnvValue([string]$path, [string]$name, [string]$value) {
  $lines = [System.Collections.Generic.List[string]]::new()
  $lines.AddRange([System.IO.File]::ReadAllLines($path, $Utf8))
  $pattern = "^\s*$([regex]::Escape($name))\s*="
  $done = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match $pattern) { $lines[$i] = "$name=$value"; $done = $true; break }
  }
  if (-not $done) { $lines.Add("$name=$value") }
  [System.IO.File]::WriteAllText($path, ($lines -join "`n") + "`n", $Utf8)
}

function Read-Secret([string]$prompt) {
  $secure = Read-Host -Prompt $prompt -AsSecureString
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr).Trim() }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

function Test-Placeholder([string]$value) { return (-not $value) -or $value.StartsWith("<") }

function Wait-Url([string]$url, [string]$what, [datetime]$deadline) {
  Write-Host -NoNewline "Waiting for $what"
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
      if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400) { Write-Host " ready."; return $true }
    } catch { }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 3
  }
  Write-Host " not ready."
  return $false
}

# --- 1. Docker ----------------------------------------------------------------------------------------------------
Write-Step "1/5 Docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Stop-Setup "Docker was not found. Install Docker Desktop (winget install Docker.DockerDesktop), start it once, then re-run."
}
$ErrorActionPreference = "Continue"
& docker compose version *> $null
$ErrorActionPreference = "Stop"
if ($LASTEXITCODE -ne 0) { Stop-Setup "Docker Compose v2 is missing. Update Docker Desktop." }
if (-not (Test-DockerRunning)) {
  $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
  if (Test-Path $desktop) {
    Write-Host "Docker is not running: starting Docker Desktop..."
    Start-Process -FilePath $desktop | Out-Null
    $until = (Get-Date).AddMinutes(3)
    while (-not (Test-DockerRunning) -and (Get-Date) -lt $until) { Start-Sleep -Seconds 3; Write-Host -NoNewline "." }
    Write-Host ""
  }
  if (-not (Test-DockerRunning)) { Stop-Setup "Docker is installed but not running. Start Docker Desktop and re-run." }
}
& docker --version
& docker compose version

# --- 2. Configuration ---------------------------------------------------------------------------------------------
Write-Step "2/5 Configuration (.env)"
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
  Write-Host ".env exists: keeping your settings."
} else {
  Copy-Item (Join-Path $PSScriptRoot ".env.example") $envFile
  Write-Host "Created .env from .env.example."
}
if (Test-Placeholder (Get-EnvValue $envFile "OPENAI_API_KEY")) {
  Write-Host "Omnific's agents use OpenAI models. Paste your OpenAI API key (typing is hidden; it is saved only in .env)."
  $key = Read-Secret "OpenAI API key (Enter to skip)"
  if ($key) {
    Set-EnvValue $envFile "OPENAI_API_KEY" $key
    Write-Host "Saved OPENAI_API_KEY to .env."
  } else {
    Write-Host "Skipped: the site will open, but new simulations fail until OPENAI_API_KEY is set in .env." -ForegroundColor Yellow
  }
  $key = $null
} else {
  Write-Host "OPENAI_API_KEY is set."
}
if (-not (Get-EnvValue $envFile "SKETCHFAB_API_TOKEN")) {
  Write-Host "Optional: a personal Sketchfab API token lets Omnific use real 3D models from Sketchfab"
  Write-Host "(sketchfab.com/settings/password). Leave it empty to use the other model libraries."
  $token = Read-Secret "Sketchfab token (Enter to skip)"
  if ($token) {
    Set-EnvValue $envFile "SKETCHFAB_API_TOKEN" $token
    $sources = Get-EnvValue $envFile "MESH_LIBRARY_SOURCES"
    if ($sources -notmatch "sketchfab") {
      Set-EnvValue $envFile "MESH_LIBRARY_SOURCES" ((@($sources -split "," | Where-Object { $_ }) + "sketchfab") -join ",")
    }
    Write-Host "Saved SKETCHFAB_API_TOKEN to .env."
  }
  $token = $null
}

if ($Gpu) { Set-EnvValue $envFile "MESH_GPU_ENABLED" "1"; Write-Host "GPU worker on: MESH_GPU_ENABLED=1 in .env." }

# --- 3. Build -----------------------------------------------------------------------------------------------------
Write-Step "3/5 Build the images"
if ($NoBuild) {
  Write-Host "Skipped (-NoBuild)."
} else {
  Write-Host "The first build downloads and builds everything (Blender, CAD and solver runners included): it can take"
  Write-Host "15-30 minutes. Later builds reuse the cache."
  Invoke-Compose -composeArgs "build"
}

# --- 4. Start -----------------------------------------------------------------------------------------------------
Write-Step "4/5 Start the stack"
Invoke-Compose -composeArgs "up", "-d"
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$apiUp = Wait-Url $ApiHealth "the API" $deadline
$siteUp = $apiUp -and (Wait-Url $SiteUrl "the website" $deadline)
if (-not $siteUp) {
  & docker compose @Profiles ps
  Stop-Setup "the site did not come up within $TimeoutMinutes minutes. See 'docker compose logs api web' for why."
}

# --- 5. Local development (optional) and open --------------------------------------------------------------------
Write-Step "5/5 Finish"
if ($Dev) {
  Write-Host "Setting up local development (setup.bat: uv workspace + web dependencies)..."
  & cmd /c "setup.bat"
  if ($LASTEXITCODE -ne 0) { Stop-Setup "setup.bat failed (see above). The website itself is running." }
}
Write-Host ""
Write-Host "Omnific is running." -ForegroundColor Green
Write-Host "  Website      $SiteUrl"
Write-Host "  Library      $SiteUrl/explore"
Write-Host "  Temporal UI  http://localhost:8233"
Write-Host ""
Write-Host "  Logs:  docker compose logs -f api web workflows"
Write-Host "  Stop:  docker compose down      (your simulations are kept in Docker volumes)"
if (-not $NoOpen) { Start-Process $SiteUrl }
