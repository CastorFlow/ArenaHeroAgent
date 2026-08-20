# 使用与配置

本文覆盖安装、凭据、运行方式、浏览器叠加层、控制字段、运行文件和常见故障。战术行为本身见 [STRATEGY.md](STRATEGY.md)。

## 1. 前置条件

- Python 3.11 或更高版本。
- Arena Hero API Key。
- 需要叠加层时使用 Chrome 或 Edge 111+。
- Windows 一键脚本需要 PowerShell 7 或 Windows PowerShell 5.1。

项目只依赖官方 `arena-hero>=0.2.9,<0.3` SDK。SDK 负责 WebSocket、HTTP、模型验证、重连和幂等重试，项目只负责战术决策。

## 2. Windows 安装

```powershell
.\setup.ps1
```

脚本会：

1. 检查 Python 版本是否至少为 3.11。
2. 在 `.venv` 中创建隔离环境。
3. 升级 `pip`。
4. 安装 `requirements.txt`。
5. 输出已安装的 Arena Hero SDK 版本。

已有虚拟环境时脚本会复用它。跳过 `pip` 自身升级：

```powershell
.\setup.ps1 -SkipPipUpgrade
```

指定 Python 可执行文件：

```powershell
.\setup.ps1 -Python "C:\Python311\python.exe"
```

## 3. 推荐运行方式：网页输入 API Key

Windows：

```powershell
.\start_all.ps1
```

脚本会启动：

- Dashboard：`http://127.0.0.1:8766/`
- 可选浏览器叠加层桥接：`http://127.0.0.1:8765/`

Dashboard 启动时 Agent 尚未运行。用户在登录页输入 Arena Hero API Key 后，服务会启动真实 `arena_hero_tactic.py` 子进程，并等待官方 SDK 鉴权成功和首个完整 Turn；成功后才创建完整默认控制配置、签发 12 小时会话并显示控制台。API Key 不写入磁盘。

停止本仓库启动的 Dashboard、Agent 和叠加层：

```powershell
.\stop_all.ps1
```

Linux / WSL2：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
./scripts/start_dashboard.sh
```

本机默认访问 `http://127.0.0.1:8766/`。VPS、自定义域名、HTTPS 和无域名 SSH 隧道见 [DEPLOYMENT.md](DEPLOYMENT.md)。

### 首次初始化的默认配置

首次成功登录且 `.arena_hero_control.json` 不存在时，服务会写入完整默认值，其中包括：

- 主模式：`develop`
- Core：不锁定、不迁移、轨道半径 0、转移模式 `star`
- 造兵队列：空；默认比例为游侠/先锋/工人 `1:1:3`
- 单位上限：工人 20，游侠/先锋 0（0 表示不设置额外上限）
- 战时存底：150
- 哈雷彗星任务：关闭

所有字段以 `.arena_hero_control.example.json` 和网页控制台当前显示为准。

## 4. 高级用法：DPAPI 或命令行直接运行 Agent

网页初始化不需要 `set_key.ps1`。只有绕过 Dashboard、直接启动 Agent 时才需要下面的凭据方式。

### Windows DPAPI

```powershell
.\set_key.ps1
.\start_arena_hero.ps1
```

Key 会保存到 `.arena_hero_api_key.dpapi`，只能由当前 Windows 用户在当前机器上解密。该文件已被 Git 忽略，但仍应像凭据一样保护。

### 环境变量或 `.env`

`arena_hero_tactic.py` 也支持：

```text
ARENA_HERO_API_KEY=your-key
```

可以临时设置环境变量，或以 `.env.example` 为模板创建本地 `.env`。`.env` 是明文文件，只适合受控环境，并已被 Git 忽略。不要在共享 shell 历史、CI 日志或截图中暴露真实 Key。

限定 Tick 数：

```powershell
.\.venv\Scripts\python.exe .\arena_hero_tactic.py --max-turns 10
```

## 5. Dashboard 参数和健康检查

```bash
python arena_hero_dashboard_server.py --host 127.0.0.1 --port 8766
```

| 参数/环境变量 | 默认值 | 作用 |
|---|---|---|
| `--host` / `ARENA_HERO_DASHBOARD_HOST` | `127.0.0.1` | Dashboard 监听地址 |
| `--port` / `ARENA_HERO_DASHBOARD_PORT` | `8766` | Dashboard 端口 |
| `--agent-status-file` / `ARENA_HERO_AGENT_STATUS_FILE` | `.arena_hero_agent_status.json` | 无凭据 Agent 生命周期状态 |
| `--agent-start-timeout` / `ARENA_HERO_AGENT_START_TIMEOUT` | `25` 秒 | 等待鉴权和首个 Turn 的上限 |
| `ARENA_HERO_DASHBOARD_TRUST_PROXY` | false | 信任反代传来的客户端 IP，仅限回环反代部署 |

公开健康检查：

```bash
curl http://127.0.0.1:8766/api/health
```

除 `/`、静态资源、`/api/login` 和 `/api/health` 外，其余 Dashboard API 都需要登录成功后签发的 Bearer 会话 Token。

## 6. 命令行参数

`arena_hero_tactic.py` 支持：

| 参数 | 默认值 | 作用 |
|---|---|---|
| `--max-turns N` | 不限制 | 成功提交 N 个 Turn 后退出 |
| `--base-url URL` | `https://api.arenahero.io` | HTTP API 根地址 |
| `--websocket-url URL` | SDK 从 HTTP 地址推导 | WebSocket 地址 |
| `--memory-file PATH` | `.arena_hero_memory.json` | 持久战术记忆 |
| `--telemetry-file PATH` | `arena_hero_telemetry.jsonl` | 每 Tick 决策遥测 |
| `--stats-file PATH` | `.arena_hero_stats.json` | 叠加层统计快照 |
| `--event-log-file PATH` | `arena_hero_events_zh.jsonl` | 中文事件日志 |

对应环境变量：

| 环境变量 | 作用 |
|---|---|
| `ARENA_HERO_API_KEY` | API Key |
| `ARENA_HERO_BASE_URL` | HTTP API 根地址 |
| `ARENA_HERO_WEBSOCKET_URL` | WebSocket 地址 |
| `ARENA_HERO_MEMORY_FILE` | 战术记忆路径 |
| `ARENA_HERO_TELEMETRY_FILE` | 遥测路径 |
| `ARENA_HERO_STATS_FILE` | 统计路径 |
| `ARENA_HERO_EVENT_LOG_FILE` | 中文日志路径 |
| `ARENA_HERO_CONTROL_FILE` | 控制 JSON 路径 |
| `ARENA_HERO_BROWSER_INTEL_FILE` | 浏览器资源提示路径 |
| `ARENA_HERO_RECOVERY_TARGETS_FILE` | 人工恢复目标列表路径 |

## 7. 浏览器叠加层

### 安装

1. 打开 `chrome://extensions` 或 `edge://extensions`。
2. 启用开发者模式。
3. 选择“加载已解压的扩展程序”。
4. 选择 `arena_hero_route_overlay` 目录。
5. 保持本地叠加层服务运行，并打开 `https://app.arenahero.io/arena`。

叠加层会读取本地路线、统计和中文事件；它也会把浏览器当前地图中的资源格作为短期、低置信提示发送给 Agent。Agent 会验证时效、距离、当前视野和资源配额合理性，不会把浏览器提示当作服务器真相。

### 控件

| 控件 | 作用 |
|---|---|
| 模式 | 在发育、侵略、抢信标之间循环 |
| 一键召回 | 所有战斗单位回 Core 防守；再次点击解除 |
| 偷袭 | 启用独立 Core 搜索/斩首编组 |
| 偷袭召回 | 只召回独立偷袭编组 |
| 统计 | 显示资源、人口、成功/失败事件和长期计数 |
| 定位 | 按单位或事件坐标聚焦地图 |
| 日志 | 显示脱敏中文事件流 |
| 设置 | 调整目标距离、偷袭编组数量和侵略编组数量 |

快捷键：

| 快捷键 | 作用 |
|---|---|
| `Alt+Shift+1` | 发育模式 |
| `Alt+Shift+2` | 侵略模式 |
| `Alt+Shift+3` | 抢信标模式 |
| `Alt+Shift+C` | 切换全军召回 |
| `Alt+Shift+R` | 切换路线显示 |
| `Alt+Shift+L` | 切换中文日志 |
| `Alt+Shift+M` | 在鼠标悬停格设置集结点 |
| `Alt+Shift+U` | 清除集结点 |

### 控制 JSON

叠加层把控制写到 `.arena_hero_control.json`。没有叠加层时也可使用 `.arena_hero_control.example.json` 作为结构参考。

| 字段 | 类型 | 含义 |
|---|---|---|
| `mode` | `develop/aggress/beacon/migrate/lightning` | 主模式 |
| `recall` | boolean | 全军召回 |
| `comet_active` | boolean | 哈雷彗星任务总开关 |
| `comet_mode` | `beacon`/`coordinate` | 追踪信标（动态坐标）/ 打击自定义坐标 |
| `comet_target` | `[x,y]` or null | coordinate 模式打击坐标（beacon 模式每 tick 从信标刷新） |
| `comet_vanguards` | non-negative integer | 小队先锋数量 |
| `comet_rangers` | non-negative integer | 小队游侠数量 |
| `comet_min_reserve_vanguards` | non-negative integer | 轨道最低满血先锋保留（保卫 Core） |
| `comet_min_reserve_rangers` | non-negative integer | 轨道最低满血游侠保留（保卫 Core） |
| `comet_wounded_threshold` | 0.0–1.0 | 半血触发替补阈值（hp/max_hp ≤ 此值即换下） |
| `beacon_target_distance` | non-negative integer | Core 希望与信标保持的曼哈顿距离；0 关闭 |
| `rally_point` | `[x,y]` or null | 战斗单位人工集结点 |
| `migration_candidate` | `[x,y]` or null | 工人验证的迁移候选格 |
| `auto_migrate` | boolean | 候选格通过防守面检查后自动进入迁移模式 |
| `aggress_vanguards` | non-negative integer | 指定侵略先锋数量；0 使用自动分配 |
| `aggress_rangers` | non-negative integer | 指定侵略游侠数量；0 使用自动分配 |
| `lightning_ring` | `[inner_r, outer_r]` | 闪电模式方环（挖空甜甜圈）`inner_r ≤ max(\|x\|,\|y\|) ≤ outer_r`；默认 `[500, 700]`，仅 `mode=lightning` 生效 |

控制文件在每个 Turn 开始时按修改时间热读取。浏览器 Manual 动作仍然按服务器规则优先于 Agent 对同一对象的动作。

## 8. 运行文件

| 文件 | 内容 | 是否应提交 |
|---|---|---|
| `.arena_hero_api_key.dpapi` | Windows 加密 Key | 否 |
| `.arena_hero_agent_status.json` | Dashboard 与 Agent 间的无凭据生命周期状态 | 否 |
| `.arena_hero_memory.json` | 地图、敌人、编队、编号和累计战术状态 | 否 |
| `.arena_hero_routes.json` | 当前 Tick 路线和单位快照 | 否 |
| `.arena_hero_stats.json` | 叠加层统计快照 | 否 |
| `.arena_hero_control.json` | 当前人工控制 | 否 |
| `.arena_hero_browser_intel.json` | 短期浏览器地图提示 | 否 |
| `.arena_hero_recovery_targets.json` | 人工资源恢复/迁移侦察点 | 否 |
| `arena_hero_telemetry.jsonl` | 每 Tick 决策和事件摘要 | 否 |
| `arena_hero_events_zh.jsonl` | 脱敏中文事件日志 | 否 |
| `agent.log`, `agent_err.log` | Agent 后台输出 | 否 |
| `arena_hero_dashboard*.log`, `arena_hero_overlay*.log` | Dashboard/叠加层输出 | 否 |

记忆、遥测和中文日志会限制文件规模；无需手工轮转即可长期运行。

## 9. 策略热加载

运行中修改 `arena_hero_strategy.py` 后：

1. 第一个观察到修改的 Tick 标记 `strategy_reload_pending=True`。
2. 下一个 Tick 保存记忆并加载候选模块。
3. 候选模块加载和状态恢复成功后才替换旧策略。
4. 新策略运行异常时跳过该 Tick，并在可能时回滚旧策略。

这避免了半写入文件或语法错误直接终止长期进程。修改连接入口 `arena_hero_tactic.py` 时仍需重启。

## 10. 故障排查

### `ProtocolError` 或状态模型字段不匹配

确认虚拟环境中的 SDK 版本：

```powershell
.\.venv\Scripts\python.exe -c "import arena_hero; print(arena_hero.__version__)"
```

本仓库要求 `>=0.2.9,<0.3`。重新运行 `setup.ps1` 或 `pip install -r requirements.txt`，然后重启 Agent。

### 叠加层无数据

依次检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/stats
```

确认扩展已启用、页面域名为 `app.arenahero.io`，并在扩展更新后点击“重新加载”。

### 资源增长慢

先看统计中的 `worker_cargo`、`visible_resource_cells`、`known_resource_cells`、`exploring_workers`、`move_failures` 和 `worker:cargo_stuck`。资源为 0 可能只是刚产兵；载货正在回仓也不等于卡住。不要只根据 Core 当前库存增加工人，必须同时考虑动态人口价格和回本周期。

### Core 门口拥堵

短暂 `cargo_queue_hold` 是主动排队。真正异常通常伴随连续 `UNIT_MOVE_FAILED`、`worker:cargo_stuck` 或载货距离长期不下降。策略会让占据 Core 的单位腾位，并在近端载货进入服务半径时暂停 Core 迁移。

### API Key 无法解密

DPAPI 文件与 Windows 用户绑定。切换账号或机器后运行：

```powershell
.\set_key.ps1
```

不要尝试把旧 DPAPI 文件复制到新机器。
