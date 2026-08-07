# Arena Hero agent launcher (foreground).
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'arena_hero_credentials.ps1')
$key = Get-ArenaHeroApiKey -Root $PSScriptRoot
try {
    $env:ARENA_HERO_API_KEY = $key
    & "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\arena_hero_tactic.py"
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:ARENA_HERO_API_KEY -ErrorAction SilentlyContinue
}
