# 设置 Arena Hero API key 并保存到 .env（一次设置，永久生效）
$ErrorActionPreference = 'Stop'

$envFile = Join-Path $PSScriptRoot '.env'
$key = Read-Host 'Arena Hero API key（将保存到 .env，以后启动自动读取，无需再输入）'

if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Error 'API key 不能为空。'
    exit 1
}

Set-Content -LiteralPath $envFile -Value "ARENA_HERO_API_KEY=$key" -Encoding utf8 -NoNewline
Write-Host "已保存到 .env（$envFile）"
Write-Host '下次启动 agent 将自动读取，无需再输入。'
