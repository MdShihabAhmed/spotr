# dev.ps1 — one-command local dev for ForcesAI on Windows.
#
# Why this exists instead of honcho: honcho sits between you and uvicorn and
# forwards stop signals badly on Windows (no real SIGKILL, no process-group
# kill), so `uvicorn --reload`'s worker orphans and honcho spins forever on
# "sending SIGKILL". Running each service in its OWN console means Ctrl+C goes
# straight to that process — uvicorn's reloader then reaps its own worker
# cleanly, the way it's designed to. `--reload` is still required on Windows
# (psycopg needs the selector event loop; see CLAUDE.md).

$root     = $PSScriptRoot
$backend  = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

# cmd /k keeps each pane open after the process exits, so a crash leaves its
# error on screen instead of vanishing. Ctrl+C stops the service and drops you
# at a prompt in the same pane.
$backendCmd  = 'uv run uvicorn app.main:app --reload --port 8000'
$frontendCmd = 'npm run dev'
$infraCmd    = 'docker compose up'

$wt = Get-Command wt.exe -ErrorAction SilentlyContinue
if ($wt) {
    # One Windows Terminal window: backend | frontend across the top,
    # infra spanning the bottom. `;` elements separate wt subcommands.
    $args = @(
        'new-tab',    '--title', 'backend',  '-d', $backend,  'cmd', '/k', $backendCmd,
        ';', 'split-pane', '-V', '--title', 'frontend', '-d', $frontend, 'cmd', '/k', $frontendCmd,
        ';', 'split-pane', '-H', '--title', 'infra',    '-d', $root,     'cmd', '/k', $infraCmd
    )
    & $wt.Source @args
    Write-Host 'Started in Windows Terminal (3 panes).' -ForegroundColor Green
    Write-Host 'Ctrl+C inside a pane stops that service cleanly. Frontend: http://localhost:5173' -ForegroundColor Green
}
else {
    # Fallback: three separate console windows.
    Start-Process cmd.exe -ArgumentList '/k', "title backend&& $backendCmd"   -WorkingDirectory $backend
    Start-Process cmd.exe -ArgumentList '/k', "title frontend&& $frontendCmd" -WorkingDirectory $frontend
    Start-Process cmd.exe -ArgumentList '/k', "title infra&& $infraCmd"       -WorkingDirectory $root
    Write-Host 'Started in 3 console windows. Ctrl+C in each to stop. Frontend: http://localhost:5173' -ForegroundColor Green
}
