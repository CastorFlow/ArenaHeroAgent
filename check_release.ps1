# Run local release checks without connecting to Arena Hero.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "The .venv environment is missing. Run setup.ps1 first."
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $FilePath $($Arguments -join ' ')"
    }
}

Push-Location $root
try {
    Invoke-Checked $python @(
        "-m", "compileall", "-q",
        "arena_hero_tactic.py",
        "arena_hero_strategy.py",
        "arena_hero_event_log.py",
        "arena_hero_route_overlay_server.py"
    )
    Invoke-Checked $python @("-m", "unittest")

    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($null -eq $node) {
        throw "Node.js is required for the overlay tests."
    }
    Invoke-Checked $node.Source @("arena_hero_route_overlay/test_overlay_core.js")
    Invoke-Checked $python @("-m", "pip", "check")

    Invoke-Checked "git" @("diff", "--check")
    Invoke-Checked "git" @("diff", "--cached", "--check")

    $publishable = @(git ls-files --cached --others --exclude-standard)
    $forbiddenNames = @(
        ".env",
        ".arena_hero_api_key.dpapi",
        ".arena_hero_memory.json",
        ".arena_hero_routes.json",
        ".arena_hero_stats.json",
        ".arena_hero_control.json",
        ".arena_hero_browser_intel.json",
        "arena_hero_telemetry.jsonl",
        "arena_hero_events_zh.jsonl",
        "agent.log",
        "agent_err.log"
    )
    $forbiddenTracked = @(
        $publishable | Where-Object {
            $leaf = Split-Path $_ -Leaf
            $forbiddenNames -contains $leaf
        }
    )
    if ($forbiddenTracked.Count -gt 0) {
        throw "Forbidden runtime or credential files are tracked: $($forbiddenTracked -join ', ')"
    }

    $secretPatterns = @(
        'AHDPAPI1:[A-Za-z0-9+/=]{20,}',
        'Bearer\s+[A-Za-z0-9._-]{12,}',
        'ARENA_HERO_API_KEY\s*=\s*[''"]?[A-Za-z0-9._-]{12,}'
    )
    $secretHits = @()
    foreach ($path in $publishable) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            continue
        }
        try {
            if (Select-String -LiteralPath $path -Pattern $secretPatterns -Quiet -ErrorAction Stop) {
                $secretHits += $path
            }
        }
        catch {
            # Ignore binary files that cannot be decoded as text.
        }
    }
    if ($secretHits.Count -gt 0) {
        throw "Possible credential material found in tracked files: $($secretHits -join ', ')"
    }

    Write-Host "Release checks passed."
}
finally {
    Pop-Location
}
