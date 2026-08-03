param(
    [switch]$ResetSavedKey
)

$ErrorActionPreference = 'Stop'

$keyPath = Join-Path $PSScriptRoot '.arena_hero_api_key.dpapi'
$hadInheritedKey = Test-Path Env:\ARENA_HERO_API_KEY
$inheritedKey = if ($hadInheritedKey) { $env:ARENA_HERO_API_KEY } else { $null }
$secureKey = $null
$keyPointer = [IntPtr]::Zero

if ($ResetSavedKey -and (Test-Path -LiteralPath $keyPath)) {
    Remove-Item -LiteralPath $keyPath -Force
}

try {
    if (-not [string]::IsNullOrWhiteSpace($inheritedKey)) {
        $secureKey = ConvertTo-SecureString $inheritedKey -AsPlainText -Force
    }
    elseif (Test-Path -LiteralPath $keyPath) {
        try {
            $encryptedKey = Get-Content -Raw -LiteralPath $keyPath -Encoding utf8
            $secureKey = ConvertTo-SecureString $encryptedKey
        }
        catch {
            Write-Warning '已保存的 Arena Hero API Key 无法解密，将重新输入并覆盖。'
            Remove-Item -LiteralPath $keyPath -Force -ErrorAction SilentlyContinue
        }
    }

    if ($null -eq $secureKey) {
        $secureKey = Read-Host 'Arena Hero API key（本次输入后将使用 Windows DPAPI 加密保存）' -AsSecureString
        if ($secureKey.Length -eq 0) {
            throw 'Arena Hero API key cannot be empty.'
        }
    }

    if (-not (Test-Path -LiteralPath $keyPath)) {
        $secureKey |
            ConvertFrom-SecureString |
            Set-Content -LiteralPath $keyPath -Encoding utf8 -NoNewline
    }

    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $env:ARENA_HERO_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
    & "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\arena_hero_tactic.py"
    exit $LASTEXITCODE
}
finally {
    if ($hadInheritedKey) {
        $env:ARENA_HERO_API_KEY = $inheritedKey
    }
    else {
        Remove-Item Env:\ARENA_HERO_API_KEY -ErrorAction SilentlyContinue
    }
    $inheritedKey = $null
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    if ($null -ne $secureKey) {
        $secureKey.Dispose()
    }
}
