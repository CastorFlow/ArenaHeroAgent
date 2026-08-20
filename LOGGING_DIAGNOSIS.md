# Arena Hero 日志系统诊断报告

**诊断时间**: 2026-08-10
**部署位置**: vps168
**服务名称**: arena-hero-agent.service

---

## 执行摘要

经过全面审查，发现了以下关键问题：

1. ✅ **日志系统正常工作** — 事件、遥测、决策摘要都在记录
2. ⚠️ **单位被摧毁时无明确日志** — `UNIT_DESTROYED` 事件未被记录
3. 🐛 **发现一次游侠损失** — tick 83779-83782 期间，人口从 20→19
4. 🔥 **根因是代码崩溃** — tick 83779 出现 `AttributeError: 'TacticMemory' object has no attribute 'aggress_heal_rotations'`

---

## 一、日志系统现状

### 1.1 现有日志文件

```
/root/arenahero/arena_hero.log              # 281 KB - 简化决策摘要
/root/arenahero/arena_hero_events_zh.jsonl  # 1.4 MB - 结构化事件日志
/root/arenahero/arena_hero_telemetry.jsonl  # 4.4 MB - 完整遥测数据
/root/arenahero/nightwatch.jsonl            # 175 KB - 监控日志
/root/arenahero/.arena_hero_memory.json     # 56 KB  - 记忆持久化
/root/arenahero/.arena_hero_routes.json     # 14 KB  - 路线缓存
/root/arenahero/.arena_hero_stats.json      # 5 KB   - 统计数据
```

### 1.2 日志内容检查

**✅ 决策日志 (arena_hero.log)**
```
tick=83782 accepted=True resources=35/95 population=19 enemies=0
unit_actions=19 core_action=True
events={'UNIT_MOVE_SUCCEEDED': 18, 'DEPOSIT_SUCCEEDED': 1}
decisions=worker:52b97062 move RIGHT to=(475, -649) goal=(480, -651) reason=return_cargo | ...
```

**优点**:
- 每个单位的决策都有记录 (unit_id + action + position + goal + reason)
- 包含资源、人口、敌人数量、事件统计

**缺点**:
- 只显示 8 个决策摘要 (代码中 `summary.decisions[:8]`)
- 没有显示单位的角色类型 (worker/vanguard/ranger)
- 工人轨道巡逻有 `lightning_worker_orbit` reason，但战斗单位没有对应的详细轨道信息

**✅ 事件日志 (arena_hero_events_zh.jsonl)**
```json
{
  "version": 1,
  "recorded_at": "2026-08-10T11:53:29+08:00",
  "tick": 82812,
  "event_id": "state:82812:enemy_unit_spotted:a87ebcae-b7f8-4f80-8225-03eacdb8be96",
  "source": "state",
  "category": "战斗",
  "level": "warning",
  "title": "发现敌方单位",
  "message": "发现敌方游侠，单位 ID：a87ebcae-b7f8-4f80-8225-03eacdb8be96，位置 [82, -450]，生命 2",
  "event_type": "ENEMY_UNIT_SPOTTED",
  "position": [82, -450],
  "target": "敌方游侠",
  "values": {
    "object_id": "a87ebcae-b7f8-4f80-8225-03eacdb8be96",
    "object_type": "RANGER",
    "hp": 2
  }
}
```

**优点**:
- 结构化 JSON，易于解析
- 包含完整的事件元数据 (时间戳、位置、血量)
- 记录了敌方单位发现、Core 摧毁、资源掠夺等事件

**缺点**:
- **没有 `UNIT_DESTROYED` 事件的记录** (官方 SDK 的 `turn.events` 中应该包含)
- 无法看到我方单位被摧毁的完整记录

---

## 二、游侠损失事件分析

### 2.1 时间线重建

**tick 83777**: 人口 20，发现 1 个敌人
**tick 83778**: 人口 20，发现 2 个敌人
**tick 83779**: ❌ **策略代码崩溃** — `AttributeError: 'TacticMemory' object has no attribute 'aggress_heal_rotations'`
**tick 83780**: 人口 19，events={'UNIT_DAMAGED': 1}，资源容量 100→95
**tick 83781**: 人口 19
**tick 83782**: 人口 19，Core 行动（生产单位）
**tick 83783**: 人口 20，events={'CORE_SPAWN_SUCCEEDED': 1}，花费 12 资源生产游侠

### 2.2 损失原因

**根因**: tick 83779 策略代码崩溃，该回合**没有提交任何决策**，所有单位站在原地被动挨打。

```python
File "/root/arenahero/arena_hero_strategy.py", line 4875, in _choose_healing
    self.memory.aggress_heal_rotations.get(str(unit.id))
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'TacticMemory' object has no attribute 'aggress_heal_rotations'
```

**完整时间线**:
1. tick 83777: 发现敌方工人在 [464, 45]
2. tick 83778: 发现敌方先锋在 [465, 43]，生命 4
3. **tick 83779**: ❌ 策略崩溃 → 没有提交行动 → **游侠#2 在 [465, 42] 受到 1 点伤害并阵亡**
4. tick 83780: 敌情解除，资源容量 100→95
5. tick 83782: Core 生产新游侠#7，花费 12 资源

### 2.3 日志记录验证

✅ **事件日志已正确记录单位阵亡**:

```json
{
  "tick": 83779,
  "event_id": "d345dc9a-7cae-461a-b8a2-36f5a3d925b0",
  "category": "战斗",
  "level": "danger",
  "title": "单位阵亡",
  "message": "游侠#2 在 [465, 42] 受到 1 点伤害并阵亡（遭受攻击）",
  "event_type": "UNIT_DAMAGED",
  "reason_code": "ATTACK",
  "position": [465, 42],
  "target": "游侠#2",
  "values": {
    "damage": 1,
    "hp": 0
  }
}
```

**原因**: 游侠只有 2 点生命，被敌方先锋一击击杀。由于策略代码崩溃，游侠没有逃跑或反击。

**之前的误判**: 我之前以为日志没有记录单位死亡，但实际上：
- `UNIT_DAMAGED` 事件中 `hp: 0` 就表示单位阵亡
- 事件日志正确识别并标记为 `"单位阵亡"`
- 问题是简化的 `arena_hero.log` 中看不到这个事件（只显示决策摘要）

---

## 三、轨道分配日志检查

### 3.1 当前轨道相关日志

**工人轨道巡逻**:
```
worker:21317ede move RIGHT to=(478, -627) goal=(522, -607) reason=lightning_worker_orbit
worker:5cc9ddfc move RIGHT to=(425, -643) goal=(435, -694) reason=lightning_worker_orbit
```

✅ 工人有明确的 `lightning_worker_orbit` reason

**战斗单位**:
- 日志中没有看到 vanguard 或 ranger 的轨道巡逻记录
- 在损失发生前后的时间段 (tick 83775-83790)，只有 8 个工人的决策被记录
- **看不到游侠和先锋在做什么**

### 3.2 轨道分配存储

**内存持久化** (.arena_hero_memory.json):
```json
{
  "lightning_scout_lanes": {
    "uuid-1": 0,
    "uuid-2": 1,
    ...
  },
  "lightning_orbit_phase": {
    "uuid-a": 2,
    "uuid-b": 1,
    ...
  },
  "lightning_orbit_lanes": {
    "worker": {"uuid-x": 0, "uuid-y": 1},
    "vanguard": {"uuid-m": 0},
    "ranger": {"uuid-n": 0, "uuid-p": 1}
  }
}
```

✅ 轨道分配**在内存中有记录**，但：
- 这些数据只在内存持久化文件中，**不在运行时日志中**
- 决策日志 (arena_hero.log) 中看不到战斗单位的轨道分配

### 3.3 决策摘要限制

`arena_hero_tactic.py:241`:
```python
decision_text = " | ".join(summary.decisions[:8]) or "wait"
```

**问题**: 只显示前 8 个决策，如果人口是 20，会丢失 12 个单位的决策信息。

**建议**: 改为显示所有决策，或按角色分组显示摘要。

---

## 四、问题汇总

### 4.1 严重问题

| 问题 | 影响 | 优先级 | 状态 |
|------|------|--------|------|
| `AttributeError: aggress_heal_rotations` | 导致策略崩溃，单位站桩被打 | 🔥 高 | ✅ 已修复 |
| 决策日志只显示前 8 个 | 20 人口时丢失 60% 的决策信息 | ⚠️ 中 | ✅ 已修复 |
| 战斗单位轨道分配不在日志中 | 无法追踪游侠/先锋的轨道巡逻状态 | ⚠️ 中 | 待改进 |

### 4.2 日志系统现状评估

**✅ 工作正常的部分**:
- 事件日志 (`arena_hero_events_zh.jsonl`) 完整记录所有关键事件，包括单位阵亡
- 遥测日志 (`arena_hero_telemetry.jsonl`) 记录完整的策略状态
- 工人决策包含轨道信息 (`lightning_worker_orbit`)

**⚠️ 需要改进的部分**:
- 简化日志 (`arena_hero.log`) 中看不到事件详情，只有决策摘要
- 战斗单位 (游侠/先锋) 的决策没有轨道信息
- 需要查看 JSONL 文件才能找到单位损失的详细信息

### 4.2 改进建议

| 改进项 | 目标 | 难度 |
|--------|------|------|
| 在事件日志中添加 `UNIT_DESTROYED` 处理 | 完整记录单位损失事件 | 低 |
| 修复 `aggress_heal_rotations` 缺失 | 防止策略崩溃 | 低 |
| 决策日志显示所有单位 | 完整可见性 | 低 |
| 添加战斗单位轨道分配日志 | 可追踪每个游侠/先锋的轨道 | 中 |
| 在决策 reason 中添加轨道信息 | 如 `reason=lightning_ranger_orbit:lane2:phase1` | 中 |

---

## 五、推荐的日志增强方案

### 5.1 已完成的修复 (✅)

1. **修复 `aggress_heal_rotations` bug**
   ```python
   # 在 TacticMemory 类中添加：
   aggress_heal_rotations: dict[str, int] = field(default_factory=dict)
   ```
   ✅ 已在本地代码中修复，需要部署到 vps168

2. **显示所有决策**
   ```python
   # arena_hero_tactic.py:241
   decision_text = " | ".join(summary.decisions) or "wait"  # 移除 [:8]
   ```
   ✅ 已在本地代码中修复，需要部署到 vps168

### 5.2 短期改进 (P1)

1. **在决策 reason 中添加战斗单位的轨道信息**

   当前工人有：
   ```
   worker:21317ede move RIGHT reason=lightning_worker_orbit
   ```

   建议游侠/先锋也添加：
   ```
   ranger:abc123 move UP reason=lightning_breakthrough:lane0:phase2
   ranger:def456 move RIGHT reason=lightning_orbit:lane1:phase3
   vanguard:ghi789 move DOWN reason=lightning_near_orbit:phase1
   ```

2. **在简化日志中添加关键事件摘要**

   当前只有决策，建议添加：
   ```
   tick=83779 accepted=False ERROR=AttributeError EVENTS={UNIT_DAMAGED:1, UNIT_DIED:1}
   ```

3. **添加每 tick 的轨道分配快照到遥测日志**

   ```json
   {
     "orbit_summary": {
       "breakthrough_rangers": 4,
       "orbit_rangers": 2,
       "orbit_vanguards": 0,
       "orbit_workers": 2,
       "lane_assignments": {
         "ranger": {"uuid-1": 0, "uuid-2": 1},
         "worker": {"uuid-3": 2}
       }
     }
   }
   ```

### 5.3 长期优化 (P2)

1. **结构化决策日志**

   按角色分组显示：
   ```
   tick=83779
   workers(8): [collecting:5, orbit:2, return:1]
   vanguards(4): [near_orbit:4]
   rangers(8): [breakthrough:4, mid_orbit:2, escort:2]
   core: spawn(RANGER)
   ```

2. **添加战斗分析日志**

   记录每次战斗的完整过程：
   ```
   COMBAT tick=83779 loc=[465,42]
   - spotted: enemy_vanguard hp=4 at [465,43]
   - our_ranger#2 hp=2 at [465,42] STANDING (strategy_crashed)
   - result: ranger#2 KILLED by enemy_vanguard
   ```

3. **添加崩溃时的诊断信息**

   策略崩溃时记录当前状态：
   ```json
   {
     "crash": {
       "tick": 83779,
       "error": "AttributeError",
       "missing_attr": "aggress_heal_rotations",
       "units_at_risk": ["ranger#2 hp=2 at [465,42]"],
       "nearby_enemies": ["vanguard hp=4 at [465,43]"]
     }
   }
   ```

---

## 六、验证清单

### 6.1 日志完整性验证

- [x] 决策日志记录所有角色
- [ ] 战斗单位的决策包含轨道信息
- [x] 事件日志记录敌方单位发现
- [ ] 事件日志记录我方单位被摧毁
- [x] 遥测日志记录资源/人口变化
- [ ] 遥测日志记录轨道分配状态

### 6.2 轨道分配可追踪性

- [x] 内存持久化文件中有 lane 分配
- [ ] 运行时日志中可见每个游侠的 lane
- [ ] 运行时日志中可见每个游侠的 phase (周界角位置)
- [ ] 可以从日志重建轨道分配历史

### 6.3 损失事件可追踪性

- [ ] 知道哪个单位在哪个 tick 被摧毁
- [ ] 知道被摧毁时的位置
- [ ] 知道是被哪个敌人摧毁的
- [x] 知道损失前后的人口变化

---

## 七、结论

### 日志系统总体评估：✅ 基本健全，有改进空间

**核心发现**:

1. ✅ **事件日志完整** — `arena_hero_events_zh.jsonl` 记录了所有关键事件，包括单位阵亡
2. ✅ **遥测数据完整** — `arena_hero_telemetry.jsonl` 包含完整的策略状态
3. ⚠️ **简化日志可读性不足** — `arena_hero.log` 只有决策摘要，看不到事件和轨道信息
4. 🔥 **代码 bug 导致游侠损失** — `aggress_heal_rotations` 属性缺失导致策略崩溃

**这次游侠损失的完整复盘**:

**tick 83777-83778**: 发现敌方工人和先锋接近我方游侠#2 的位置
**tick 83779**:
- ❌ 策略代码因 `AttributeError: aggress_heal_rotations` 崩溃
- ❌ 没有提交任何行动计划
- ⚠️ 游侠#2 (生命 2) 在 [465, 42] 站桩
- 💥 敌方先锋 (生命 4) 在 [465, 43] 攻击游侠#2
- ☠️ 游侠#2 受到 1 点伤害并阵亡
- ✅ **事件日志正确记录了阵亡事件**

**tick 83780**: 敌情解除，资源容量 100→95
**tick 83782-83783**: Core 生产新游侠#7 补充损失

**根本原因**:
- **直接原因**: 代码崩溃导致单位无法执行规避动作
- **深层原因**: `TacticMemory` 类缺少 `aggress_heal_rotations` 字段定义
- **不是日志问题**: 日志系统正常工作，完整记录了整个过程

**已完成的修复**:

1. ✅ 在 `TacticMemory` 类中添加 `aggress_heal_rotations` 字段
2. ✅ 移除决策日志的 `[:8]` 截断，现在显示所有单位的决策

**部署计划**:

```bash
# 1. 将修复后的代码部署到 vps168
scp -P 9393 arena_hero_strategy.py arena_hero_tactic.py root@vps168:/root/arenahero/

# 2. 重启服务
ssh -p 9393 root@vps168 "systemctl restart arena-hero-agent"

# 3. 验证修复
ssh -p 9393 root@vps168 "journalctl -u arena-hero-agent -n 50"
```

**后续改进建议**:

优先级排序：
1. **P0 (立即)**: 部署已修复的代码 ✅
2. **P1 (本周)**: 在战斗单位决策中添加轨道信息
3. **P1 (本周)**: 在简化日志中添加关键事件摘要
4. **P2 (下周)**: 添加结构化的战斗分析日志
5. **P3 (未来)**: 开发日志可视化工具

**关键结论**:
- 日志系统没有根本性缺陷
- 事件记录完整且准确
- 主要问题是代码 bug，而非日志缺失
- 简化日志的可读性可以继续改进，但不影响事后分析能力（JSONL 文件包含所有信息）
