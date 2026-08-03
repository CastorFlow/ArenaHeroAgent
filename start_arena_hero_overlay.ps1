param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$server = Join-Path $PSScriptRoot 'arena_hero_route_overlay_server.py'
$routes = Join-Path $PSScriptRoot '.arena_hero_routes.json'

& $python $server --routes-file $routes --port $Port
exit $LASTEXITCODE
