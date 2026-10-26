@echo off
rem CodeRabbit local review of this repo, using the official CLI installed in a WSL distro.
rem (CodeRabbit also ships a native Windows installer: irm https://cli.coderabbit.ai/install.ps1 ^| iex.
rem  This wrapper is for the WSL install.) One-time: scripts\coderabbit-review.cmd auth login
rem Usage:
rem   scripts\coderabbit-review.cmd                       review uncommitted changes (plain text)
rem   scripts\coderabbit-review.cmd --agent               structured findings (what Claude Code uses)
rem   scripts\coderabbit-review.cmd --committed --base main   review commits since main instead
rem   scripts\coderabbit-review.cmd auth login^|status     any other coderabbit command is passed through
rem Env: CODERABBIT_WSL_DISTRO (default Ubuntu); CODERABBIT_WSL_USER (default: the distro's default user)
setlocal EnableDelayedExpansion
set "DISTRO=%CODERABBIT_WSL_DISTRO%"
if "%DISTRO%"=="" set "DISTRO=Ubuntu"
set "USEROPT="
if not "%CODERABBIT_WSL_USER%"=="" set "USEROPT=-u %CODERABBIT_WSL_USER%"
rem wsl --cd takes the Windows path directly; -e runs bash without another shell parsing the line.
rem The bash script is FIXED text: caller arguments arrive as "$@" and are never evaluated as code.
rem It finds the CLI for whichever user runs it (PATH, else ~/.local/bin) and sets git config so the Windows
rem checkout's CRLF/permissions/ownership don't show up as changes.
set "WSLRUN=wsl -d %DISTRO% %USEROPT% --cd "%~dp0.." -e bash -lc"
set "BASHSCRIPT=cr=$(command -v coderabbit || echo ~/.local/bin/coderabbit); export GIT_CONFIG_COUNT=3 GIT_CONFIG_KEY_0=core.autocrlf GIT_CONFIG_VALUE_0=true GIT_CONFIG_KEY_1=core.filemode GIT_CONFIG_VALUE_1=false GIT_CONFIG_KEY_2=safe.directory GIT_CONFIG_VALUE_2=*; exec \"$cr\" \"$@\""

set "FIRST=%~1"
set "ARGS=%*"
rem "review findings" reads stored results; it must not get -c (which takes several files and would start a review)
if /I "%FIRST%"=="review" if /I "%~2"=="findings" goto :passthrough
if /I "%FIRST%"=="review" (
  rem "review ..." is the same as no subcommand
  set "ARGS="
  for /f "tokens=1,* delims= " %%a in ("%*") do set "ARGS=%%b"
) else if not "%FIRST%"=="" if not "%FIRST:~0,1%"=="-" goto :passthrough

rem default scope = the pending (uncommitted) change set, unless the caller chose one
set "SCOPE=--uncommitted"
for %%A in (!ARGS!) do (
  if /I "%%~A"=="--committed" set "SCOPE="
  if /I "%%~A"=="--uncommitted" set "SCOPE="
  if /I "%%~A"=="--base" set "SCOPE="
  if /I "%%~A"=="--base-commit" set "SCOPE="
  if /I "%%~A"=="--remote" set "SCOPE="
)
%WSLRUN% "!BASHSCRIPT!" coderabbit review -c CLAUDE.md %SCOPE% !ARGS!
exit /b %errorlevel%

:passthrough
%WSLRUN% "!BASHSCRIPT!" coderabbit %*
exit /b %errorlevel%
