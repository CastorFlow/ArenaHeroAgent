> **历史文档提示：** 本文描述的是已退出运行时的轨道/突破行为，不是当前策略规范。当前行为请以 `README.md`、`docs/STRATEGY.md`、`docs/USAGE.md` 以及现行代码为准。

# Arena Hero 轨道分配机制完整分析

## 📊 执行摘要

你的 agent **确实设计了开路轨道**，但**当前被安全阈值禁用了**。

**关键发现**：
- ✅ 开路轨道已设计：前 4 个游侠应分配到开路轨道（绕原点外大环）
- ⚠️ **当前未启用**：Core 距原点约 650 格，超过安全阈值 400 格
- 📋 所有 6 个游侠都在执行**中轨巡逻**（绕 Core 的行星轨道）

---

## 🎯 四层轨道体系设计

你的策略设计了一个**四层同心轨道系统**：

### 1. 开路轨道（Breakthrough Orbit）- 恒星维度

**目的**：绕原点 (0,0) 外大环探索，提前点亮 Core 轨道的资源

**配置**：
```python
LIGHTNING_BREAKTHROUGH_RING_OFFSET = 12        # 比 Core 轨道外扩 12 格
LIGHTNING_BREAKTHROUGH_SLOT_COUNT = 4          # 固定 4 个开路游侠
LIGHTNING_BREAKTHROUGH_MAX_CORE_DISTANCE = 400 # 安全阈值
```

**分配逻辑**：
- **前 4 个游侠**（按 UUID 排序）应分配到开路轨道
- 半径 = `pr + BREAKTHROUGH_RING_OFFSET + lane_idx * gap_r`
  - `pr` = Core 巡逻半径（约 650）
  - `BREAKTHROUGH_RING_OFFSET` = 12
  - `gap_r` = 游侠视野半径 5
  - 4 个游侠分别在：662、667、672、677 半径

**安全条件**：
```python
core_origin_dist = distance(core.position, (0, 0))
breakthrough_safe = core_origin_dist <= LIGHTNING_BREAKTHROUGH_MAX_CORE_DISTANCE
```

**当前状态**：
- Core 距原点约 **650 格** > 400 格安全阈值
- ❌ **开路轨道被禁用**
- 所有游侠改为执行中轨巡逻

---

### 2. 近轨（Near Orbit）- 先锋专属

**半径**：5 格（`LIGHTNING_NEAR_ORBIT_RADIUS`）

**圆心**：Core 位置

**职责**：贴 Core 视野边缘转圈护卫

**当前状态**：✅ 2 个先锋在近轨执行

---

### 3. 中轨（Mid Orbit）- 游侠主力

**半径**：动态计算，从先锋外层开始，每个游侠间隔 5 格（视野半径）

**圆心**：Core 位置

**职责**：围 Core 中层护卫 + 分层防御

**当前状态**：✅ 6 个游侠全部在中轨（半径 14、19、24）

---

### 4. 远轨（Far Orbit）- 工人探索

**半径**：游侠外层开始，每个工人间隔 3 格

**圆心**：Core 位置

**职责**：闲时上轨点亮迷雾，发现资源时优先采集

**当前状态**：⚠️ 部分工人在远轨，部分在采集/返回

---

## 🔄 游侠分配优先级

### 设计规则（代码 `_choose_rangers_lightning`）

```python
ordered_rangers = sorted(turn.rangers, key=_uuid_key)  # 按 UUID 排序
core_origin_dist = _distance(turn.core.position, (0, 0))
breakthrough_safe = core_origin_dist <= 400

for index, ranger in enumerate(ordered_rangers):
    # 前 4 个游侠 → 开路轨道（如果安全）
    if index < 4 and breakthrough_safe:
        target = _lightning_breakthrough_target(turn, ranger, index)
        # ... 执行开路巡逻

    # 第 5+ 个游侠 → 中轨巡逻
    else:
        mid_lane = index - 4 if breakthrough_safe else index
        target = _lightning_orbit_waypoint(turn, ranger, UnitType.RANGER, mid_lane)
        # ... 执行中轨巡逻
```

### 当前实际执行（Core 距原点 650 > 400）

```python
breakthrough_safe = False  # 距离超过安全阈值

# 所有 6 个游侠都走 else 分支
for index in range(6):
    mid_lane = index  # 直接用 index 作为 lane
    # ranger#0 → mid_lane 0 (半径 14)
    # ranger#1 → mid_lane 1 (半径 14)
    # ranger#2 → mid_lane 2 (半径 19)
    # ranger#3 → mid_lane 3 (半径 19)
    # ranger#4 → mid_lane 0 (半径 24)
    # ranger#5 → mid_lane 1 (半径 24)
```

---

## 📈 后续添加游侠/工人的分配规则

### 游侠分配（假设 Core 仍在方环外侧）

#### 情况 A：Core 距原点 > 400（当前状态）

**第 7 个游侠**：
- `index = 6`
- `breakthrough_safe = False`
- `mid_lane = 6`
- 分配到**中轨第 7 层**（半径约 24 + 5 = 29）

**第 8-20 个游侠**：
- 继续按 `mid_lane = index` 分配
- 每增加一个游侠，外扩一个轨道层（间隔 5 格）

**外圈优先混合策略**：
- 先铺开外层轨道（最大化领土覆盖）
- 同一半径的单位通过 `phase_offset` 错开四角
- 轨道分配由 `_lightning_calculate_outer_first_orbits` 计算

#### 情况 B：Core 距原点 ≤ 400（开路轨道启用）

**第 7 个游侠**：
- `index = 6`
- `breakthrough_safe = True`
- 前 4 个已在开路轨道
- `mid_lane = 6 - 4 = 2`
- 分配到**中轨第 3 层**（与现在的 ranger#2 同层）

**总结**：
- **前 4 个**：开路轨道（绕原点外大环，半径 662-677）
- **第 5+ 个**：中轨巡逻（绕 Core，半径从 14 开始）

---

### 工人分配

工人轨道从游侠外层开始，间隔 3 格（工人视野半径）：

```python
# 代码：_lightning_assign_orbit_lanes (line 7023-7031)
vg_outer = NEAR_ORBIT_RADIUS + max(0, vg_count - 1) * gap_v  # 先锋最外层
rk_inner = vg_outer + gap_r  # 游侠起始层
rk_outer = rk_inner + max(0, rk_count - 1) * gap_r  # 游侠最外层
worker_inner = rk_outer + 3  # 工人起始层
```

**当前（6 游侠，12 工人）**：
- 先锋最外层：5 + (2-1) * 4 = 9
- 游侠起始：9 + 5 = 14
- 游侠最外层：14 + (6-1) * 5 = 39
- **工人起始**：39 + 3 = **42**

**添加第 13 个工人**：
- 分配到远轨（半径从 42 开始，间隔 3 格）
- 实际半径由 `_lightning_calculate_outer_first_orbits` 动态计算
- 优先外层铺开，然后加密

**工人职责**：
- ✅ 优先采集资源（发现资源节点时离轨采集）
- ⚠️ 闲时才上远轨巡逻
- 🛡️ 防御时回近轨当肉盾

---

## 🔍 开路轨道何时启用？

### 触发条件

```python
core_origin_dist <= LIGHTNING_BREAKTHROUGH_MAX_CORE_DISTANCE  # 400 格
```

### 三种情况

1. **Core 刚出生**（距原点 < 400）
   - ✅ 开路轨道启用
   - 前 4 个游侠绕原点外大环探索

2. **Core 移动到方环边缘**（距原点约 650）
   - ❌ 开路轨道禁用（当前状态）
   - 所有游侠改为绕 Core 护卫

3. **Core 返回内圈**（距原点 < 400）
   - ✅ 开路轨道重新启用
   - 前 4 个游侠立即切换到开路轨道

### 设计理念

**为什么设 400 格阈值？**

- Core 在方环外侧（距原点约 650 格）时：
  - 开路游侠需要在 **662-677 半径**巡逻
  - 距 Core 可能 **100-200 格**
  - 孤军深入，被击杀风险高
  - "提前点亮资源"的意义不大（Core 已远离原点）

- Core 在内圈（距原点 < 400 格）时：
  - 开路游侠在 **412-427 半径**巡逻
  - 距 Core 只有 **12-27 格**
  - 可快速回防，相对安全
  - 能提前点亮 Core 轨道将经过的资源

---

## 📊 当前状态总结

### 轨道分配矩阵

| 角色类型 | 数量 | 轨道类型 | 半径范围 | 圆心 | 状态 |
|---------|------|---------|---------|------|------|
| 先锋 | 2 | 近轨 | 5 | Core | ✅ 到位 |
| 游侠 | 6 | 中轨 | 14-24 | Core | ✅ 全部到位 |
| 工人 | 12 | 后勤/远轨 | 42+ | Core | ⚠️ 混合任务 |
| **开路游侠** | **0** | **开路轨道** | **662-677** | **(0,0)** | **❌ 未启用** |

### 为什么没看到开路游侠？

**原因**：
```python
core_origin_dist = 650  # Core 当前距原点约 650 格
breakthrough_safe = (650 <= 400)  # False
```

**代码路径**：
```python
# arena_hero_strategy.py:10338-10343
for index, ranger in enumerate(ordered_rangers):
    # 前 4 个游侠
    if index < 4 and breakthrough_safe:  # False，不执行
        # ... 开路轨道逻辑
    else:  # ← 所有游侠都走这里
        # ... 中轨巡逻逻辑
```

---

## 🚀 添加游侠后的预期行为

### 场景 1：Core 保持在方环外侧（距原点 650）

**第 7 个游侠**：
- ✅ 分配到中轨第 7 层（半径约 29）
- 绕 Core 巡逻，与前 6 个游侠一起形成多层防御

**第 8-20 个游侠**：
- 继续外扩中轨层（每层间隔 5 格）
- 最终形成 20 层同心方环（半径 14-109）

### 场景 2：Core 移回内圈（距原点 < 400）

**立即重新分配**：
- **前 4 个游侠**：切换到开路轨道（绕原点外大环）
- **第 5-20 个游侠**：中轨巡逻（绕 Core）

**第 7 个游侠**：
- 分配到中轨第 3 层（`mid_lane = 6 - 4 = 2`）
- 与 ranger#2 同层，但 `phase_offset` 错开

---

## 🛠️ 开路轨道职责（设计但未启用）

### 探索任务

- **绕原点外大环巡逻**（半径 662-677）
- **提前点亮资源**（Core 轨道将经过的区域）
- **侦察敌方 Core**（无守卫或弱守卫）

### 选择性交战规则

✅ **可以打**（游击战）：
- 1v1 敌方先锋（利用射程优势）
- 无守卫的敌方 Core

❌ **立即撤退**：
- 见到敌方游侠
- 敌方战斗单位数量 > 我方开路游侠数量
- 目标 Core 有游侠守卫

🚫 **绝对不能**：
- 离开开路轨道范围
- 千里追击

### 勤王机制

**NEAR 威胁**（敌人进入近轨 r=5）：
- 所有开路游侠**立即回防**
- 沿途绕过敌人，不交战
- 退到 Core 附近与工人配合阻击

---

## 📝 代码溯源

### 开路轨道配置

```python
# arena_hero_strategy.py:101-106
LIGHTNING_BREAKTHROUGH_RING_OFFSET = 12
LIGHTNING_BREAKTHROUGH_SLOT_COUNT = 4
LIGHTNING_BREAKTHROUGH_MAX_CORE_DISTANCE = 400
```

### 开路轨道目标点计算

```python
# arena_hero_strategy.py:6852-6913
def _lightning_breakthrough_target(self, turn, ranger, lane_idx):
    """开路轨道(恒星维度,绕原点外大同心方环)下一目标点。"""
    pr = self._lightning_patrol_radius()  # Core 巡逻半径
    gap_r = 5  # 游侠视野半径
    radius = pr + 12 + lane_idx * gap_r  # 开路轨道半径
    corners = ((radius, radius), (radius, -radius), ...)
    # ... 四角巡逻逻辑
```

### 游侠分配决策

```python
# arena_hero_strategy.py:10334-10343
core_origin_dist = _distance(turn.core.position, (0, 0))
breakthrough_safe = core_origin_dist <= 400

for index, ranger in enumerate(ordered_rangers):
    # 开路游侠（前4个）
    if index < 4 and breakthrough_safe:
        target = self._lightning_breakthrough_target(turn, ranger, index)
        # ... 开路巡逻
    # 中轨游侠（第5+个）
    else:
        mid_lane = index - 4 if breakthrough_safe else index
        target = self._lightning_orbit_waypoint(turn, ranger, UnitType.RANGER, mid_lane)
        # ... 中轨巡逻
```

---

## 🎯 总结

### 你的问题解答

**Q1: 我的 agent 应该还设计了开路轨道吧？**
- ✅ **是的**，设计了开路轨道（`LIGHTNING_BREAKTHROUGH_*` 系列配置）

**Q2: 为什么没有往上面分配游侠？**
- ⚠️ **安全阈值触发**：Core 距原点 650 > 400，开路轨道被禁用

**Q3: 后续添加游侠将如何分配？**
- **Core 在方环外侧**（当前）：继续分配到中轨，外扩新层
- **Core 返回内圈**：前 4 个游侠切换到开路轨道，第 5+ 个中轨

**Q4: 添加工人将如何分配？**
- 分配到远轨（从半径 42 开始，间隔 3 格）
- 优先采集资源，闲时才上轨巡逻

---

## 🔧 建议

### 如果想启用开路轨道

**选项 1：调整安全阈值**
```python
# 将 400 提高到 700，允许在方环外侧启用开路
LIGHTNING_BREAKTHROUGH_MAX_CORE_DISTANCE = 700
```

**选项 2：等待 Core 移动**
- Core 自然移动回内圈（< 400 格）时自动启用

**选项 3：保持当前设计**
- 当前的"距离禁用"是合理的安全机制
- 避免游侠孤军深入被击杀

### 监控命令

查看 Core 距原点距离：
```bash
# 从巡逻点推算（pr ≈ 650 即 Core 在方环外侧）
journalctl -u arena-hero-agent -n 50 | grep "lightning patrol waypoint"

# 查看游侠分配（breakthrough vs mid_orbit）
journalctl -u arena-hero-agent -n 50 | grep "ranger:"
```

---

**报告完成时间**：2026-08-10
**数据来源**：vps168 日志 (tick 84151) + 本地代码分析
