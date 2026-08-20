# 动态轨道排斥防御、医疗轮换与 Core 锚定设计

> 状态：待实现设计稿
> 适用模式：当前强制启用的 lightning 模式
> 编制假设：后期以 **3 Ranger : 1 Worker** 为主，并维持少量 Vanguard（当前策略通常维持 1 名）。

## 1. 背景与目标

当前项目实际上强制走 lightning 分支；旧模式中较成熟的部分战斗逻辑没有被 lightning 分支复用。现有系统的主要问题是：

1. 防御分级依赖 `NEAR=6 / MID=20 / FAR=40` 等固定 Core 距离，无法跟随动态轨道扩张。
2. 普通 lightning Ranger 射击没有复用旧 recall 分支的 `assigned_damage` 机制，静止目标会被过度补刀。
3. Ranger 在 NEAR/MID 分支会直接移动/回防，跳过本 Tick 本可执行的射击。
4. Vanguard 在常规巡逻下对局部敌军偏向后撤，缺乏“敌人进入我方轨道即主动排斥”的统一规则。
5. Ranger 只有 2 HP；受伤后虽然已有通用治疗回 Core 行为，但没有替补、没有医疗空位管理，也没有 Core 服务锚定。
6. Worker 肉盾条件当前等价于“敌战斗单位在 Core 20 格内”，会让空手工人大规模、无组织地回防，既破坏经济，也不能形成有效卡位。
7. Core 当前有“移动时避开更靠近敌战斗单位的方向”的评分惩罚，但没有把伤员服务、内层漏斗防线、补盾/补兵需求作为硬性停驻条件。

本设计将战术从“敌人接近 Core 才保守回防”改为：

> **轨道排斥防御（Orbital Repulsion）**：敌人一旦进入我方当前动态轨道的有效警戒范围，即进入战斗调度；敌人越向内穿透，Ranger 集结规模、集火强度、Worker 屏障和 Vanguard 承诺程度越高。敌人被赶出警戒范围后，部队及时复位，避免无限追击。

并将其与：

- Ranger 1 HP 医疗撤离与 ETA 替补；
- Worker 漏斗卡位；
- Vanguard 内环承诺战斗；
- Core 避敌与战时/医疗锚定；

整合为同一个防线调度系统。

---

## 2. 当前轨道几何：必须读取最终 lane，而非使用静态距离

### 2.1 当前实际轨道分配

当前 lightning 分配逻辑中：

- Vanguard 独立近轨，半径：

```text
R_vanguard = LIGHTNING_NEAR_ORBIT_RADIUS = 5
```

- Ranger 与 Worker 共用中外轨：

```text
R_layer(n) = 10 + 5 × (n - 1), n >= 1
```

- 每层容量为 `2n`，并以初始三层循环队列填充。
- Ranger 占共享序列的前段，Worker 紧随其后；故 Ranger 总体位于内侧，Worker 总体位于外侧。

以 16 人、12 Ranger + 4 Worker 为例，当前算法可产生：

```text
r=10: 2
r=15: 4
r=20: 6
r=25: 3
r=30: 1

Ranger 覆盖 r=10 / 15 / 20
Worker 覆盖 r=25 / 30
```

最外层可能很稀疏，不能再用固定“附近 8 格”召集 Ranger。

### 2.2 事实来源

威胁系统每 Tick 应直接读取：

```python
lanes = self._lightning_assign_shared_middle_lanes(turn)
```

并推导：

```text
R_ranger_inner = min(Ranger lane radius)
R_ranger_outer = max(Ranger lane radius)
R_sensor_outer = max(Ranger 与 Worker 的 lane radius)
G = 已使用的相邻轨道半径差的中位数；无数据时回退到当前轨道 gap
```

不要仅凭 `_lightning_calculate_outer_first_orbits()` 的函数参数重算战术边界；其 `vision_radius`、`min_units_per_orbit`、`ideal_interval` 在当前实际分配中并未充分参与结果。

### 2.3 两种距离

当前轨道是方形环；应同时保留两种坐标：

```text
r_inf = max(abs(enemy.x - core.x), abs(enemy.y - core.y))
d1    = abs(enemy.x - core.x) + abs(enemy.y - core.y)
```

- `r_inf`：判断敌人穿过了哪层方形轨道；
- `d1`：评估真实移动时间、到 Core 的压力和 ETA。

---

## 3. 动态威胁区：从固定 NEAR/MID/FAR 改为轨道派生

### 3.1 派生边界

```text
R_commit = R_vanguard + ceil((R_ranger_inner - R_vanguard) / 2)
R_screen = R_vanguard + ceil((R_commit - R_vanguard) / 2)
```

典型 `R_vanguard=5, R_ranger_inner=10` 时：

```text
R_commit = 8
R_screen = 7
```

`R_commit` 是先锋和 Core 的“退无可退”承诺线；`R_screen` 是 Worker 优先形成屏障的内环带。

### 3.2 威胁等级

| 等级 | 动态条件 | 战术含义 | 默认响应 |
|---|---|---|---|
| T0 轨外 | `r_inf > R_sensor_outer + V_outer` | 不在我方轨道有效警戒层 | 仅保留敌情记忆 |
| T1 预警 | `R_ranger_outer + V_ranger < r_inf <= R_sensor_outer + V_outer` | 外层 Worker/轨道发现敌情 | 报点；ETA 最短的 1–2 Ranger 预置，不全队回撤 |
| T2 排斥 | `R_ranger_outer < r_inf <= R_ranger_outer + V_ranger` | 进入 Ranger 外层视野/火力缓冲 | 可射即射；否则组织局部火力组和火力位 |
| T3 突破 | `R_commit < r_inf <= R_ranger_outer` | 已穿入 Ranger 环 | 强集火、Ranger 补位、Worker 开始按 ETA 建屏障、Vanguard 向威胁扇区截击 |
| T4 Core 危急 | `r_inf <= R_commit`，或敌人对 Core 建立攻击 ETA 很短 | 退无可退 | Core 锚定；Vanguard 承诺；Worker 漏斗/肉盾；所有可及时射击的满血 Ranger 优先火力 |

`V_outer` 应由实际最外有效观察层角色确定；Worker 视野与 Ranger 视野不同。Worker 外轨是**预警幕**，不是要求全军决战的主战边界。

### 3.3 敌人威胁分

对同一扇区的敌人按以下因素排序：

1. 当前威胁等级（T4 > T3 > T2 > T1）；
2. 敌方角色（Ranger > Vanguard > Worker > Core）；
3. 到 Core 建立攻击的 ETA；
4. 向内移动趋势；
5. 同扇区敌军规模；
6. 是否即将穿过下一道动态边界。

敌 Core 不应压过正在侵入轨道的敌战斗单位；只有在无本土入侵或可安全压制时才作为次级目标。

---

## 4. Ranger：全局火控、医疗撤离和 ETA 替补

### 4.1 Ranger 生命状态

| 状态 | 条件 | 行为 |
|---|---|---|
| READY | `hp == 2` | 可参加射击、拦截、补位、巡逻 |
| MEDIVAC | `hp == 1` | 默认撤回 Core 治疗，退出常规前线编组 |
| LAST_STAND | `hp == 1` 且 T4 防线会立即破裂 | 仅允许一次高置信度关键掩护射击；不可继续普通追击或充当前排 |

默认：Ranger 掉到 1 HP 就登记 `MEDIVAC`。例外必须严格：若本 Tick 可高置信度击杀会立刻突破/攻击 Core 的关键敌人，允许先射一次；否则优先安全回撤。

### 4.2 医疗空位与轮换

对每个 `MEDIVAC` Ranger 创建 `Vacancy`：

```text
- 所在扇区
- 原轨道半径 / 原职责
- 当前或应接管的火力位
- 威胁等级
```

计算：

```text
T_home        = 伤员到 Core 的安全路径 ETA
T_queue       = Core 医疗队列/停车位延迟
T_heal        = 缺失 HP 的治疗行动时长（1 HP Ranger 通常为一次）
T_return      = 治疗后回到原扇区、轨道或新火力位的 ETA
T_medical_gap = T_home + T_queue + T_heal + T_return

T_relief(r)   = 满血、未承诺 Ranger 到 Vacancy 接替火力位的 ETA
```

选择替补的必要条件：

```text
T_relief < T_medical_gap
且 T_relief <= 敌人穿过下一道防线的 ETA
且 抽调后 Core 内层保有当前威胁等级所需的最低满血 Ranger 数。
```

替补排序：

1. 同扇区同/邻近轨道；
2. 相邻扇区 ETA 最短者；
3. 更内层但不造成 Core 防线短缺者；
4. 远侧 Ranger 最后才使用。

若来不及由 Ranger 替补，则不强迫 1 HP Ranger 留在前线；由 Worker 屏障、其余健康 Ranger 收缩火力位和 Vanguard 内环承诺来填补空档。

### 4.3 Ranger 火控：必须先分配射击，再分配移动

每 Tick 统一建立 `ShotLedger`，而不是让 Ranger 单独决定：

```text
ShotIntent = (ranger_id, enemy_id, expected_cell, hit_confidence)
```

保持并扩展已有的移动预测候选。新增：Worker 漏斗门提供的 `gate_cell` 应成为高置信预测来源。

```text
assigned_expected_damage[enemy_id]
assigned_expected_damage[enemy_id][expected_cell]
assigned_shot_cells[enemy_id]
```

射击分配顺序：

1. T4/T3 中即将威胁 Core 的敌军；
2. 敌方 Ranger；
3. 敌方 Vanguard；
4. 敌方 Worker；
5. 敌 Core。

原则：

- 静止或高置信目标达到有效 HP 所需伤害后，后续 Ranger 应转移目标，避免过杀；
- 移动目标可对主要预测格保留额外的备用/侧翼射击，但不能无限堆枪；
- **任何威胁等级下，只要有高价值合法射击，就优先射击。** 当前 NEAR/MID“先移动后 return、跳过射击”的逻辑必须删除。

未被分配射击的 READY Ranger 才去抢火力位。火力位需满足：

```text
- 对目标有横、竖或 45° 对角合法射线；
- 射程为 2–3；
- 中间无障碍；
- 不踏入敌 Vanguard 相邻格；
- 尽量规避敌 Ranger 已知射线；
- 与友军形成不同射线/不同预测格的交叉火力；
- 根据 ETA 与扇区，避免整支部队跨地图误响应。
```

### 4.4 响应者选择：ETA，而非固定局部半径

对每个 threat，枚举能形成合法火力位的 Ranger，计算：

```text
ETA(ranger, threat) = 到任一合法射击位的可达 ETA
```

只有能在敌军穿过下一层边界前赶到的 Ranger 才进入响应池。这样解决外轨稀疏问题：

- 不会因“8 格内没有别人”让孤立 Ranger 无援；
- 也不会因把半径粗暴放大到 20 而抽空对侧轨道。

同一扇区优先，邻接扇区其次，再按 ETA 补充。

---

## 5. Vanguard：近轨锚点和承诺防线

### 5.1 基本规则

Vanguard 每 Tick 的第一优先级：若存在有效相邻 `SWEEP` 目标，则先扫击。评分应同时考虑：

```text
敌 Core > 敌 Ranger > 敌 Vanguard > 敌 Worker
同格多个敌人额外加分。
```

普通 lightning 巡逻中“发现局部威胁就退向 Core”的行为应取消。外层 T1/T2 只做威胁扇区侧移/预置，不追到 Worker 外轨。

### 5.2 承诺状态

当：

```text
enemy.r_inf <= R_commit
或 enemy 对 Core 建立攻击 ETA <= Core 单次移动承诺时间
```

Vanguard 进入 `COMMITTED`：

```text
- 不再因拉开距离而朝 Core 后撤；
- 只允许向敌人—Core 之间的截击点移动；
- 允许横移到更优 sweep 格或漏斗门内侧；
- 任何相邻有效攻击优先 SWEEP。
```

Vanguard 是最终硬锚，不是外圈追兵。

---

## 6. Worker：按 ETA 的漏斗屏障、肉盾和战时补充

### 6.1 角色定位

Worker 外轨是预警幕，不是主战线。Worker 仅在 T3/T4 或可及时形成屏障时进入防御调度；带货 Worker 仍优先回 Core，除非 T4 已直接威胁 Core。

现有“Core 20 格内所有空手 Worker 肉盾”应删除，改为按实际位置、ETA 和屏障格数量挑选少数空手 Worker。

### 6.2 漏斗（Funnel / Gate）

在威胁扇区，以 `R_screen` 附近为优先内层带：

1. 枚举敌人朝 Core 前进或能建立 Core 攻击的合法下一步格；
2. 选一个 `gate_cell`，使其：
   - 能被至少 1–2 个健康 Ranger 迅速建立合法射线；
   - 不会让敌先锋直接压到 Core；
   - 有 Vanguard 可从内侧扫击/接管；
3. 其余能快速接近 Core 的候选格为 `block_cells`；
4. 选 ETA 合格的 Worker 占住 `block_cells`，保留 `gate_cell`。

效果：敌军被迫优先走向少数预定格，Ranger 可以高概率预测下一位置并集中射击。

### 6.3 Worker 选择与轮换

候选 Worker：

```text
- 无 cargo；
- 未被医疗、回仓或更高优先级任务占用；
- 到 block_cell 的 ETA 不晚于敌人到该格的 ETA；
- 移动后不破坏更内层已有屏障；
- 不占用 Core 出生/治疗关键格。
```

```text
worker_screen_need = min(
    有效 block_cells 数量,
    能在敌到达前抵达的空手 Worker 数量
)
```

1 HP 屏障 Worker：

- 替补已到或敌暂未贴近：让位、撤出；
- 敌下一步就会突破：允许作为一次性最后屏障；
- 同时登记 `worker_screen_shortfall`，请求下一名 Worker 或临时生产。

### 6.4 生产应急覆盖

Core 生产逻辑在 T4 下允许临时越过 3:1 增长比例：

```text
if worker_screen_shortfall > 0:
    优先生成 Worker
elif ranger_relief_shortfall > 0:
    优先生成 Ranger
else:
    维持原有 3 Ranger : 1 Worker 与死亡补同种逻辑
```

Core 自身治疗和直接威胁下的护盾修理仍优先于普通生产。

---

## 7. Core：外层避敌，医疗/战斗时锚定

### 7.1 当前缺口

现有 Core 逻辑已有避敌倾向：移动候选点会惩罚“走得更靠近敌方 Ranger/Vanguard”的方向。也有货物、低 HP、低护盾等停驻条件。

但缺少：

- 伤员即将抵达 Core 时禁止开启新移动；
- Worker 漏斗、Vanguard 承诺、屏障缺口时禁止巡逻移动；
- 明确的敌方攻击 ETA 与 Core 单次移动时间比较。

Core 一次移动承诺持续多个 Tick；移动中会影响治疗、补盾、生产、交付和防线锚点，不能在内层战斗时作为常规逃跑手段。

### 7.2 状态机

#### MOBILE_EVADE

适用：敌军仅 T1/T2，Core 状态良好，无伤员服务窗口，无屏障建设需求，敌人到 Core 攻击 ETA 明显大于一次 Core 移动承诺时间。

行为：可以继续巡逻/小范围移动，但移动评分必须避开靠近敌方战斗单位的方向；不应为了远处观察目标频繁开四 Tick 移动。

#### MEDICAL_ANCHOR

满足任一条即禁止启动新移动：

```text
- 伤员已在 Core 格；
- 伤员将在 Core 单次移动完成前抵达；
- 医疗停车/队列存在；
- Core 周围有需要维持的治疗或补位服务。
```

形式化：

```text
若 min(T_patient_to_core) <= T_core_move_commit：Core 锚定。
```

#### COMBAT_ANCHOR

满足任一条即锚定：

```text
- 敌人处于 T4；
- 敌人到当前 Core 攻击格的 ETA <= T_core_move_commit；
- Vanguard 已 COMMITTED；
- Worker 漏斗正在建立或已建立；
- worker_screen_shortfall > 0；
- Core 需要紧急治疗/修盾；
- Ranger 医疗/替补系统仍在紧急运行。
```

### 7.3 锚定时 Core 优先级

```text
1. Core 自身治疗
2. 直接威胁下的护盾修理
3. 紧急屏障 Worker 生产
4. Ranger 替补生产
5. 正常 3:1 生产
6. 仅在无服务需求与无内层压力时恢复巡逻
```

必须继续尊重 Core 格的生产容量与医疗停车位，避免伤员占满生产位置；伤员排队应使用现有/改进的 Core 周边停车位逻辑。

---

## 8. 推荐的计划与执行顺序

当前 `_choose_healing()` 过早直接让伤员行动，导致后续角色无法围绕空位和防线统一调度。应把它重构为“计划阶段的一部分”。

推荐每 Tick：

```text
1. 更新轨道 lane 几何（Ranger/Worker/Vanguard 实际分层）
2. 构建敌方 threat，并按扇区聚合
3. 分诊：标记 MEDIVAC Ranger，建立 Vacancy，计算医疗 ETA
4. 选 Ranger relief，计算 ranger_relief_shortfall
5. 规划 Worker Funnel，计算 block_cells / gate_cell / worker_screen_shortfall
6. 建立全局 Ranger ShotLedger；先分配射击，再分配未射击者的火力位/补位
7. 规划 Vanguard：先 sweep，再按 T3/T4 截击/承诺
8. 规划 Worker：屏障、轮换、回仓、轨道
9. 根据医疗与战斗计划确定 CoreAnchorState，并执行治疗/修盾/生产/移动
10. 无任务单位回到其动态轨道
```

所有行动仍需通过现有 `MovementPlanner` 统一处理障碍、敌方占格和最终占用，避免角色间互相卡位。

---

## 9. 实施建议：模块划分

建议新增或重构出明确 helper，而非继续把判断散落在 `_choose_rangers_lightning()`、`_choose_vanguards_lightning()`、`_choose_workers()` 中：

```python
_lightning_orbit_geometry(turn)
    # 返回 lane 半径、R_commit、R_screen、各扇区覆盖信息

_analyze_orbital_threats(turn, geometry)
    # 聚合敌情；给出 zone、角色威胁、Core ETA、下一层穿透 ETA

_plan_ranger_triage_and_relief(turn, threats, geometry)
    # MEDIVAC、Vacancy、T_medical_gap、ETA 替补

_plan_worker_funnel(turn, threats, geometry, triage)
    # gate_cell、block_cells、Worker 屏障与缺口

_allocate_lightning_ranger_fire(turn, threats, funnel, triage)
    # ShotLedger、assigned expected damage、预测格覆盖

_plan_vanguard_commitment(turn, threats, funnel, geometry)

_core_anchor_state(turn, threats, triage, funnel)
```

应尽量使用 dataclass 表示：

```text
OrbitGeometry
ThreatContact
RangerTriage / Vacancy / ReliefAssignment
FunnelPlan
ShotIntent / ShotLedger
CoreAnchorState
```

---

## 10. 验收测试

至少补充以下测试（单位测试或策略模拟测试）：

1. **动态边界**：3:1 人口从 12、16、20、24 增长时，`R_ranger_outer`、`R_sensor_outer` 与 T1–T4 边界随 lane 变化，不能保持固定 20/40。
2. **外轨稀疏支援**：敌人由外侧 Worker 看见，但附近没有第二个 Ranger；系统能按 ETA 从同/邻扇区调 1–2 名 Ranger，而不是依赖固定半径 8。
3. **残血 Ranger**：2 HP Ranger 受一点伤后登记 MEDIVAC、执行安全回 Core；不会继续承担普通追击。
4. **Ranger 替补**：伤员撤离后，若健康 Ranger 的 `T_relief < T_medical_gap`，能接管其扇区火力位；若来不及，不抽空 Core 内层。
5. **火控去过杀**：两个 HP 的静止敌 Ranger 不会被全部 Ranger 重复分配射击；移动目标仍保留合理的备用预测射击。
6. **T3/T4 射击优先**：Ranger 有合法高价值射击时，不会因回撤/截击分支而跳过射击。
7. **Vanguard 承诺**：敌人进入 `R_commit` 后，Vanguard 不再向 Core 后退；有相邻目标时优先 sweep。
8. **Worker 漏斗**：Worker 根据真实位置和 ETA 占 `block_cells`，保留可被 Ranger 覆盖的 `gate_cell`；带货 Worker 不被无条件抽走。
9. **Core 医疗锚定**：伤员将在 Core 移动完成前抵达时，Core 不启动巡逻移动。
10. **Core 战斗锚定**：T4、屏障建设、Vanguard COMMITTED 或 Core 攻击 ETA 短时，Core 不移动，并优先治疗/修盾/紧急补屏障单位。
11. **排斥后的复位**：敌人离开 `R_sensor_outer + V_outer` 或连续若干 Tick 丢失视野后，响应 Ranger、Worker、Vanguard 回归各自轨道/任务，不无限追击。

---

## 11. 非目标与约束

- 不把 Worker 外轨变为必须全军死守的边界；它是预警幕。
- 不让 1 HP Ranger 长时间承担前排；LAST_STAND 仅是极窄例外。
- 不让 Vanguard 追击至 Worker 外层；它是近轨锚点。
- 不在内层战斗期间让 Core 以常规巡逻方式“逃跑”。
- 不依赖硬编码 `6 / 20 / 40 / 8 / 10` 作为攻击边界；固定数值仅可保留游戏规则固有值，例如 Ranger 射程/视野、Core 单次移动耗时等。
- 必须遵守 Arena Hero 行动规则：每单位每 Tick 一个动作、移动先结算、攻击同时结算、Ranger 的射击需预测目标格、障碍阻挡射线/视野等。
