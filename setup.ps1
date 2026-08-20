param(
    [string]$Python = "python",
    [switch]$SkipPipUpgrade
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

$pythonCommand = Get-Command $Python -ErrorAction SilentlyContinue
if ($null -eq $pythonCommand) {
    throw "Python was not found. Install Python 3.11 or newer, then rerun setup.ps1."
}

$versionText = & $pythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to run Python from '$($pythonCommand.Source)'."
}
$version = [Version]$versionText.Trim()
if ($version -lt [Version]"3.11") {
    throw "Python 3.11 or newer is required; found $version."
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $pythonCommand.Source -m venv (Join-Path $root ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the .venv virtual environment."
    }
}

if (-not $SkipPipUpgrade) {
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }
}

& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Arena Hero dependencies."
}

& $venvPython -c "import arena_hero; print(f'Arena Hero SDK {arena_hero.__version__} is ready.')"
Write-Host "Setup complete. Run .\start_all.ps1, then enter the Arena Hero API Key at http://127.0.0.1:8766/."
