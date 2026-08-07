$ErrorActionPreference = 'Stop'

if (-not ('System.Security.Cryptography.ProtectedData' -as [type])) {
    Add-Type -AssemblyName System.Security
}

function ConvertFrom-ArenaHeroSecureString {
    param([Parameter(Mandatory = $true)][System.Security.SecureString]$SecureValue)

    return [System.Net.NetworkCredential]::new('', $SecureValue).Password
}

function Remove-ArenaHeroKeyFromDotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $remaining = @(
        Get-Content -LiteralPath $Path -Encoding utf8 |
            Where-Object { $_ -notmatch '^\s*ARENA_HERO_API_KEY\s*=' }
    )
    if ($remaining.Count -eq 0) {
        Remove-Item -LiteralPath $Path -Force
    }
    else {
        Set-Content -LiteralPath $Path -Value $remaining -Encoding utf8
    }
}

function Save-ArenaHeroApiKey {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $plainBytes = [Text.Encoding]::UTF8.GetBytes($Key)
    try {
        $encryptedBytes = [Security.Cryptography.ProtectedData]::Protect(
            $plainBytes,
            $null,
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        $encrypted = 'AHDPAPI1:' + [Convert]::ToBase64String($encryptedBytes)
        Set-Content -LiteralPath $Path -Value $encrypted -Encoding ascii -NoNewline
    }
    finally {
        [Array]::Clear($plainBytes, 0, $plainBytes.Length)
    }
}

function Get-ArenaHeroApiKey {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$ResetSavedKey
    )

    $encryptedPath = Join-Path $Root '.arena_hero_api_key.dpapi'
    $envPath = Join-Path $Root '.env'

    if (-not $ResetSavedKey -and (Test-Path -LiteralPath $encryptedPath)) {
        try {
            $encrypted = Get-Content -LiteralPath $encryptedPath -Raw -Encoding ascii
            if ($encrypted.StartsWith('AHDPAPI1:')) {
                $encryptedBytes = [Convert]::FromBase64String($encrypted.Substring(9))
                $plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
                    $encryptedBytes,
                    $null,
                    [Security.Cryptography.DataProtectionScope]::CurrentUser
                )
                try {
                    $key = [Text.Encoding]::UTF8.GetString($plainBytes)
                }
                finally {
                    [Array]::Clear($plainBytes, 0, $plainBytes.Length)
                }
            }
            else {
                # Migrate the legacy ConvertFrom-SecureString representation.
                $secure = ConvertTo-SecureString -String $encrypted
                $key = ConvertFrom-ArenaHeroSecureString -SecureValue $secure
                Save-ArenaHeroApiKey -Key $key -Path $encryptedPath
            }
        }
        catch {
            throw 'Cannot decrypt the saved Arena Hero API Key. Run set_key.ps1 to replace it.'
        }
        if ([string]::IsNullOrWhiteSpace($key)) {
            throw 'The saved Arena Hero API Key is empty. Run set_key.ps1 to replace it.'
        }
        Remove-ArenaHeroKeyFromDotEnv -Path $envPath
        return $key
    }

    $key = $null
    if (-not $ResetSavedKey) {
        $key = $env:ARENA_HERO_API_KEY
        if ([string]::IsNullOrWhiteSpace($key) -and (Test-Path -LiteralPath $envPath)) {
            foreach ($line in Get-Content -LiteralPath $envPath -Encoding utf8) {
                if ($line -match '^\s*ARENA_HERO_API_KEY\s*=\s*(.+?)\s*$') {
                    $key = $Matches[1].Trim().Trim('"').Trim("'")
                    break
                }
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($key)) {
        $secure = Read-Host 'Arena Hero API Key (stored with Windows encryption)' -AsSecureString
        $key = ConvertFrom-ArenaHeroSecureString -SecureValue $secure
    }
    if ([string]::IsNullOrWhiteSpace($key)) {
        throw 'API Key cannot be empty.'
    }

    Save-ArenaHeroApiKey -Key $key -Path $encryptedPath
    Remove-ArenaHeroKeyFromDotEnv -Path $envPath
    return $key
}
