# Replace the Arena Hero API Key saved with Windows DPAPI.
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'arena_hero_credentials.ps1')
Get-ArenaHeroApiKey -Root $PSScriptRoot -ResetSavedKey | Out-Null
Write-Host 'API Key saved with Windows DPAPI. Future launches will reuse it.'
