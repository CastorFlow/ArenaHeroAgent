# Arena Hero agent 启动脚本（前台，可见日志）
# key 读取顺序：环境变量 ARENA_HERO_API_KEY → .env 文件 → 提示输入并保存到 .env
$ErrorActionPreference = 'Stop'

$envFile = Join-Path $PSScriptRoot '.env'
$key = $env:ARENA_HERO_API_KEY

if ([string]::IsNullOrWhiteSpace($key) -and (Test-Path -LiteralPath $envFile)) {
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding utf8) {
        if ($line -match '^ARENA_HERO_API_KEY=(.+)$') {
            $key = $Matches[1].Trim().Trim('"').Trim("'")
            break
        }
    }
}

if ([string]::IsNullOrWhiteSpace($key)) {
    $key = Read-Host 'Arena Hero API key（将保存到 .env，以后启动无需再输入）'
    if ([string]::IsNullOrWhiteSpace($key)) {
        throw 'API key 不能为空。'
    }
    Set-Content -LiteralPath $envFile -Value "ARENA_HERO_API_KEY=$key" -Encoding utf8 -NoNewline
}

$env:ARENA_HERO_API_KEY = $key
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\arena_hero_tactic.py"
exit $LASTEXITCODE
