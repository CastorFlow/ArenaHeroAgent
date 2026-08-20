# Stop only Arena Hero processes started from this repository.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$entries = @(
    [IO.Path]::GetFullPath((Join-Path $root 'arena_hero_dashboard_server.py')),
    [IO.Path]::GetFullPath((Join-Path $root 'arena_hero_route_overlay_server.py')),
    [IO.Path]::GetFullPath((Join-Path $root 'arena_hero_tactic.py'))
)

$processes = @(
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object {
            $commandLine = $_.CommandLine
            $null -ne $commandLine -and ($entries | Where-Object { $commandLine -like "*$_*" })
        }
)

foreach ($process in $processes) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host "Stopped $($processes.Count) Arena Hero project process(es)."
