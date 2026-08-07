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

## 3. 凭据

### Windows DPAPI（推荐）

```powershell
.\set_key.ps1
```

Key 会保存到 `.arena_hero_api_key.dpapi`，只能由当前 Windows 用户在当前机器上解密。该文件已被 Git 忽略，但仍应像凭据一样保护，不应发送给他人。

重新设置 Key 时再次运行 `set_key.ps1`。

### 环境变量或 `.env`

`arena_hero_tactic.py` 也支持：

```text
ARENA_HERO_API_KEY=your-key
```

可以临时设置环境变量，或以 `.env.example` 为模板创建本地 `.env`。`.env` 是明文文件，只适合受控环境，并已被 Git 忽略。Windows 启动脚本发现明文 `.env` Key 后会将它迁移到 DPAPI 并从 `.env` 删除该行。

程序不会把 Key 写入遥测、中文事件日志或叠加层响应。

## 4. 运行 Agent

### 前台运行

Windows：

```powershell
.\start_arena_hero.ps1
```

直接运行 Python：

```powershell
.\.venv\Scripts\python.exe .\arena_hero_tactic.py
```

前台模式会逐 Tick 输出资源、人口、敌人数、动作数、上个 Tick 的事件和前几条战术决策。按 `Ctrl+C` 可正常停止。

### 后台运行 Agent 与叠加层

```powershell
.\start_all.ps1
```

该脚本只停止并替换当前仓库路径下旧的 `arena_hero_tactic.py` 和 `arena_hero_route_overlay_server.py` 进程，不会匹配其他 Python 项目。输出写入：

- `agent.log`
- `agent_err.log`

叠加层服务地址为 `http://127.0.0.1:8765`。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

### 停止

```powershell
.\stop_all.ps1
```

### 限定 Tick 数

适合短时间试运行：

```powershell
.\.venv\Scripts\python.exe .\arena_hero_tactic.py --max-turns 10
```

## 5. 跨平台运行

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
export ARENA_HERO_API_KEY='your-key'
python arena_hero_tactic.py
```

不要在共享 shell 历史、CI 日志或截图中暴露真实 Key。生产环境应使用平台自己的 secret store 注入环境变量。

叠加层服务可以独立启动：

```bash
python arena_hero_route_overlay_server.py --port 8765
```

浏览器扩展目前固定连接 `127.0.0.1:8765`。

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
| `mode` | `develop/aggress/beacon/migrate` | 主模式 |
| `recall` | boolean | 全军召回 |
| `raid_enabled` | boolean | 独立偷袭编组 |
| `raid_recall` | boolean | 只召回偷袭编组 |
| `raid_vanguards` | non-negative integer | 偷袭先锋数 |
| `raid_rangers` | non-negative integer | 偷袭游侠数 |
| `beacon_target_distance` | non-negative integer | Core 希望与信标保持的曼哈顿距离；0 关闭 |
| `rally_point` | `[x,y]` or null | 战斗单位人工集结点 |
| `migration_candidate` | `[x,y]` or null | 工人验证的迁移候选格 |
| `auto_migrate` | boolean | 候选格通过防守面检查后自动进入迁移模式 |
| `aggress_vanguards` | non-negative integer | 指定侵略先锋数量；0 使用自动分配 |
| `aggress_rangers` | non-negative integer | 指定侵略游侠数量；0 使用自动分配 |

控制文件在每个 Turn 开始时按修改时间热读取。浏览器 Manual 动作仍然按服务器规则优先于 Agent 对同一对象的动作。

## 8. 运行文件

| 文件 | 内容 | 是否应提交 |
|---|---|---|
| `.arena_hero_api_key.dpapi` | Windows 加密 Key | 否 |
| `.arena_hero_memory.json` | 地图、敌人、编队、编号和累计战术状态 | 否 |
| `.arena_hero_routes.json` | 当前 Tick 路线和单位快照 | 否 |
| `.arena_hero_stats.json` | 叠加层统计快照 | 否 |
| `.arena_hero_control.json` | 当前人工控制 | 否 |
| `.arena_hero_browser_intel.json` | 短期浏览器地图提示 | 否 |
| `.arena_hero_recovery_targets.json` | 人工资源恢复/迁移侦察点 | 否 |
| `arena_hero_telemetry.jsonl` | 每 Tick 决策和事件摘要 | 否 |
| `arena_hero_events_zh.jsonl` | 脱敏中文事件日志 | 否 |
| `agent.log`, `agent_err.log` | 后台进程输出 | 否 |

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
