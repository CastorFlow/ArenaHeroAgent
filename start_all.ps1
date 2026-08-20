param(
    [bool]$OpenBrowser = $true
)

# Start the API-key dashboard and optional route-overlay bridge in the background.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw 'The .venv environment is missing. Run .\setup.ps1 first.'
}

$entries = @(
    [IO.Path]::GetFullPath((Join-Path $root 'arena_hero_dashboard_server.py')),
    [IO.Path]::GetFullPath((Join-Path $root 'arena_hero_route_overlay_server.py')),
    [IO.Path]::GetFullPath((Join-Path $root 'arena_hero_tactic.py'))
)

$old = @(
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object {
            $commandLine = $_.CommandLine
            $null -ne $commandLine -and ($entries | Where-Object { $commandLine -like "*$_*" })
        }
)
foreach ($process in $old) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
if ($old.Count -gt 0) {
    Start-Sleep -Milliseconds 800
}

function Join-NativeArguments {
    param([Parameter(Mandatory = $true)][string[]]$Values)
    return (($Values | ForEach-Object { '"' + ($_ -replace '"', '\"') + '"' }) -join ' ')
}

$overlayEntry = Join-Path $root 'arena_hero_route_overlay_server.py'
$overlayArguments = Join-NativeArguments @(
    $overlayEntry,
    '--routes-file', (Join-Path $root '.arena_hero_routes.json'),
    '--stats-file', (Join-Path $root '.arena_hero_stats.json'),
    '--control-file', (Join-Path $root '.arena_hero_control.json'),
    '--logs-file', (Join-Path $root 'arena_hero_events_zh.jsonl'),
    '--browser-intel-file', (Join-Path $root '.arena_hero_browser_intel.json'),
    '--port', '8765'
)
Start-Process -FilePath $python `
    -ArgumentList $overlayArguments `
    -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $root 'arena_hero_overlay.log') `
    -RedirectStandardError (Join-Path $root 'arena_hero_overlay_err.log') `
    -WindowStyle Hidden

$dashboardEntry = Join-Path $root 'arena_hero_dashboard_server.py'
$dashboardArguments = Join-NativeArguments @(
    $dashboardEntry,
    '--host', '127.0.0.1',
    '--port', '8766',
    '--control-file', (Join-Path $root '.arena_hero_control.json'),
    '--stats-file', (Join-Path $root '.arena_hero_stats.json'),
    '--telemetry-file', (Join-Path $root 'arena_hero_telemetry.jsonl'),
    '--battle-file', (Join-Path $root 'arena_hero_battle_history.jsonl'),
    '--trail-file', (Join-Path $root 'arena_hero_core_trail.jsonl'),
    '--routes-file', (Join-Path $root '.arena_hero_routes.json'),
    '--events-file', (Join-Path $root 'arena_hero_events_zh.jsonl'),
    '--agent-status-file', (Join-Path $root '.arena_hero_agent_status.json')
)
Start-Process -FilePath $python `
    -ArgumentList $dashboardArguments `
    -WorkingDirectory $root `
    -RedirectStandardOutput (Join-Path $root 'arena_hero_dashboard.log') `
    -RedirectStandardError (Join-Path $root 'arena_hero_dashboard_err.log') `
    -WindowStyle Hidden

Start-Sleep -Milliseconds 800
$dashboardUrl = 'http://127.0.0.1:8766'
Write-Host 'Arena Hero services started:'
Write-Host "  dashboard: $dashboardUrl"
Write-Host '  overlay:   http://127.0.0.1:8765'
Write-Host 'Enter the Arena Hero API Key in the dashboard; the Agent starts only after validation.'
Write-Host "Stopped $($old.Count) old project process(es)."

if ($OpenBrowser) {
    Start-Process $dashboardUrl
}
