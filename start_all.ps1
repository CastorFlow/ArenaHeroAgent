# Arena Hero 一键启动（后台运行，关闭本窗口不影响 agent 与 overlay server）
# 用法：powershell -ExecutionPolicy Bypass -File start_all.ps1
$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot

# 1. overlay server（8765 端口，供 Chrome 扩展读取路线/状态/控制）
Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList "$root\arena_hero_route_overlay_server.py", `
        "--routes-file", "$root\.arena_hero_routes.json", `
        "--stats-file", "$root\.arena_hero_stats.json", `
        "--control-file", "$root\.arena_hero_control.json", `
        "--port", "8765" `
    -WorkingDirectory $root `
    -WindowStyle Hidden

Start-Sleep -Milliseconds 500

# 2. agent（连接游戏跑策略；自动读取 .env 中的 key；日志写入 agent.log）
Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList "$root\arena_hero_tactic.py" `
    -WorkingDirectory $root `
    -RedirectStandardOutput "$root\agent.log" `
    -RedirectStandardError "$root\agent_err.log" `
    -WindowStyle Hidden

Write-Host "Arena Hero 已启动（后台）:"
Write-Host "  - overlay server  → http://127.0.0.1:8765"
Write-Host "  - agent           → 日志见 agent.log"
Write-Host "关闭本窗口不影响以上进程运行。"
