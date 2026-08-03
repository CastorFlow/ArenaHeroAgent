# Arena Hero 一键启动：overlay server + agent（重启电脑后只需运行本脚本）
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot

# 1. overlay server（8765 端口，供 Chrome 扩展读取路线/状态/控制）
Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList "$root\arena_hero_route_overlay_server.py", `
        "--routes-file", "$root\.arena_hero_routes.json", `
        "--stats-file", "$root\.arena_hero_stats.json", `
        "--control-file", "$root\.arena_hero_control.json", `
        "--port", "8765" `
    -WindowStyle Hidden

Start-Sleep -Milliseconds 800

# 2. agent（连接游戏跑策略；自动读取已保存的 API key）
Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", `
        "-File", "$root\start_arena_hero.ps1" `
    -WindowStyle Hidden

Write-Host "Arena Hero 已启动：overlay server + agent"
Write-Host "提示：首次启动如 key 未保存会提示输入 API key（只输一次）"
