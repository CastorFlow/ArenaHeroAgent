# Stop only the Arena Hero agent and overlay started from this repository.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$agentEntry = [IO.Path]::GetFullPath((Join-Path $root "arena_hero_tactic.py"))
$overlayEntry = [IO.Path]::GetFullPath((Join-Path $root "arena_hero_route_overlay_server.py"))

$processes = @(
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object {
            $_.CommandLine -like "*$agentEntry*" -or
            $_.CommandLine -like "*$overlayEntry*"
        }
)

foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
}

Write-Host "Stopped $($processes.Count) Arena Hero project process(es)."
