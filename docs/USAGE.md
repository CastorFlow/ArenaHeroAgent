# 使用与控制参数

本项目只有一个顶层策略：**Lightning**。Dashboard 和浏览器叠加层不再提供其他顶层模式切换。

## 1. 登录并启动 Agent

### Windows

```powershell
.\setup.ps1
.\start_all.ps1
```

访问：

```text
http://127.0.0.1:8766/
```

### Linux / WSL2

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
./scripts/start_dashboard.sh
```

默认同样访问：

```text
http://127.0.0.1:8766/
```

登录框中输入 Arena Hero API Key。服务端会：

1. 将 Key 仅通过子进程环境传给 `arena_hero_tactic.py`；
2. 等待官方 SDK 鉴权并收到首个可操作 Turn；
3. 成功后创建 Lightning 默认控制配置；
4. 返回随机 Dashboard 会话 Token。

无效、停用或无法完成初始化的 Key 不会进入控制台。

## 2. 12 小时滑动会话

12 小时限制针对的是**浏览器 Dashboard 会话 Token**，不是 Arena Hero API Key，也不是 Agent 的运行时长。

- 每次成功的已认证 Dashboard 请求都会把 Token 的失效时间重置为“当前时间 + 12 小时”。
- 页面保持打开并正常轮询时，会话会持续续期。
- 最后一次成功请求后连续 12 小时没有活动，Token 才失效。
- Token 失效后重新输入同一个 API Key，即可获得新会话并复用仍在运行的 Agent。
- 会话失效、关闭浏览器或关闭电脑上的网页，不会停止 VPS 上的 Agent。
- 停止 Dashboard 服务会停止它监管的 Agent 子进程；Agent 崩溃或显式停止服务也会结束运行。

浏览器不会保存 API Key，只会在当前标签页的 `sessionStorage` 保存随机 Token。

## 3. Dashboard 与本地叠加层

- Dashboard：`http://127.0.0.1:8766/`
- 可选扩展桥接：`http://127.0.0.1:8765/`

8765 仅用于本机 Chrome/Edge 扩展读取路线、统计、事件与提交控制，不应暴露到公网。

Dashboard 可查看或修改：

- 当前 Tick、资源、人口、单位和 Agent 运行状态；
- Lightning 路线、Core 轨迹、战况和事件；
- Core 轨道、转移与敌情偏置；
- 生产队列、比例、上限、补兵和战备资源；
- Comet 小队任务。

控制配置会写入 `.arena_hero_control.json`，不包含 API Key。Agent 每个 Turn 都会检查控制文件变化。

## 4. 控制字段

默认配置见 `.arena_hero_control.example.json`。

### 4.1 Core

| 字段 | 类型 / 范围 | 默认值 | 说明 |
|---|---|---:|---|
| `core_orbit_radius` | 非负整数 | `0` | Core 围绕原点的方形轨道半径；`0` 表示不启用自动轨道巡逻 |
| `core_hold` | boolean | `false` | 强制 Core 驻扎，停止非必要移动 |
| `core_target` | `[x, y]` 或 `null` | `null` | 用户指定的 Core 转移目标 |
| `core_transfer_mode` | `star/march/fortify` | `star` | Core 转移期间的工人物流样式 |
| `core_evade_enemies` | boolean | `false` | 可见敌人出现时优先远离；与追击同时开启时退避优先 |
| `core_chase_enemies` | boolean | `false` | 可见敌人出现时增加靠近敌人的移动偏置 |
| `core_pursue_beacon` | boolean | `false` | 让 Core 把当前信标位置作为临时转移目标 |

Core 转移样式：

- `star`：工人继续采集并向移动中的 Core 交付。
- `march`：空载工人随 Core 急行，减少远距离采集拖延。
- `fortify`：工人继续采集但在转移完成前暂缓集中交付。

这些值只是 Lightning 内部的 Core 行为样式，不是顶层模式。

### 4.2 生产

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `build_queue` | 单位类型数组 | `[]` | 最多 20 个预定单位；值为 `WORKER`、`VANGUARD`、`RANGER` |
| `spawn_ratio` | 三个非负整数 | `1:1:3` | 游侠、先锋、工人的长期目标比例；某项为 `0` 表示停止常规生产该兵种，全部为 `0` 表示囤资源 |
| `unit_caps` | 三个非负整数 | 工人 `20`，其余 `0` | 兵种独立上限；`0` 表示不设置该兵种上限 |
| `replenish_threshold` | 三个非负整数 | 全部 `0` | 数量低于阈值时触发补兵 |
| `replenish_priority` | 单位类型顺序 | 游侠、工人、先锋 | 多个兵种同时不足时的补兵优先级 |
| `wartime_reserve` | 非负整数 | `150` | 经济允许时保留的治疗、修盾和补兵资源 |

生产选择顺序大体为：预定队列、开局工人引导、紧急防线/医疗缺口、补兵阈值、兵种上限、正常比例。实际生产仍受当前资源、人口价格、Core 状态和 Core 所在格容量限制。

### 4.3 Comet 小队

| 字段 | 类型 / 范围 | 默认值 | 说明 |
|---|---|---:|---|
| `comet_active` | boolean | `false` | 开启或关闭 Comet 任务 |
| `comet_mode` | `beacon/coordinate` | `beacon` | 追踪当前信标，或前往自定义坐标 |
| `comet_target` | `[x, y]` 或 `null` | `null` | `coordinate` 目标；目标无效时任务不会出发 |
| `comet_vanguards` | 非负整数 | `3` | 期望派出的先锋数 |
| `comet_rangers` | 非负整数 | `3` | 期望派出的游侠数 |
| `comet_min_reserve_vanguards` | 非负整数 | `3` | Core 周边最低保留先锋数 |
| `comet_min_reserve_rangers` | 非负整数 | `3` | Core 周边最低保留游侠数 |
| `comet_wounded_threshold` | `0.0` 到 `1.0` | `0.5` | 生命比例低于阈值时进入撤退/替补流程 |
| `comet_rally_enabled` | boolean | `false` | 首批成员是否先集合再推进 |
| `comet_rally_distance` | 非负整数 | `0` | 集合点相对目标的回退距离 |

`beacon` 在这里表示 Comet 的目标来源，不是顶层策略模式。

## 5. 配置文件示例

```json
{
  "core_orbit_radius": 0,
  "core_hold": false,
  "core_target": null,
  "core_transfer_mode": "star",
  "core_evade_enemies": false,
  "core_chase_enemies": false,
  "core_pursue_beacon": false,
  "build_queue": [],
  "spawn_ratio": {"ranger": 1, "vanguard": 1, "worker": 3},
  "unit_caps": {"worker": 20, "vanguard": 0, "ranger": 0},
  "replenish_threshold": {"ranger": 0, "vanguard": 0, "worker": 0},
  "replenish_priority": ["ranger", "worker", "vanguard"],
  "wartime_reserve": 150,
  "comet_active": false,
  "comet_mode": "beacon",
  "comet_target": null,
  "comet_vanguards": 3,
  "comet_rangers": 3,
  "comet_min_reserve_vanguards": 3,
  "comet_min_reserve_rangers": 3,
  "comet_wounded_threshold": 0.5,
  "comet_rally_enabled": false,
  "comet_rally_distance": 0
}
```

建议优先通过 Dashboard 修改，避免手写 JSON 时产生类型或拼写错误。

## 6. 运行文件

常见运行文件：

| 文件 | 内容 |
|---|---|
| `.arena_hero_control.json` | 当前 Lightning 控制参数 |
| `.arena_hero_agent_status.json` | Agent 初始化与生命周期状态，不含 Key |
| `.arena_hero_memory.json` | 持久化战术记忆 |
| `.arena_hero_routes.json` | 单位路线与目标 |
| `.arena_hero_stats.json` | 脱敏统计 |
| `.arena_hero_browser_intel.json` | 本地扩展提供的低可信资源提示 |
| `arena_hero_telemetry.jsonl` | Tick 遥测 |
| `arena_hero_events_zh.jsonl` | 中文事件 |
| `arena_hero_battle_history.jsonl` | 战况历史 |
| `arena_hero_core_trail.jsonl` | Core 轨迹 |

这些文件和日志默认被 Git 忽略。不要把它们当作公开示例提交，因为其中可能包含账号运行状态或地图信息。

## 7. 停止与重新登录

Windows：

```powershell
.\stop_all.ps1
```

systemd：

```bash
sudo systemctl stop arena-hero-dashboard
```

同一个 API Key 重复登录会复用仍在运行的 Agent并签发新的网页会话。已有 Agent 正在运行时，使用不同 API Key 登录会返回冲突；如确实要切换账号，应先停止或重启 Dashboard 服务。

## 8. 无域名远程访问

VPS 上不要直接公开 8766。使用 SSH 隧道：

```bash
ssh -L 8766:127.0.0.1:8766 <user>@<vps>
```

本机浏览器打开：

```text
http://127.0.0.1:8766/
```

域名和 HTTPS 部署见 [DEPLOYMENT.md](DEPLOYMENT.md)。
