param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$server = Join-Path $PSScriptRoot 'arena_hero_route_overlay_server.py'
$routes = Join-Path $PSScriptRoot '.arena_hero_routes.json'
$stats = Join-Path $PSScriptRoot '.arena_hero_stats.json'
$control = Join-Path $PSScriptRoot '.arena_hero_control.json'

& $python $server --routes-file $routes --stats-file $stats --control-file $control --port $Port
exit $LASTEXITCODE
