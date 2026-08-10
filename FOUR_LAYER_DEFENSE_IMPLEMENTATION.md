# 四层轨道职责 + 分层防御系统 - 实施总结

**实施日期**: 2026-08-10
**版本**: v1.0

## 概览

本次实施完成了Arena Hero战术系统的重大升级，实现了四层轨道职责分工和三级分层防御机制，解决了轨道密度不合理导致的视野漏洞问题。

---

## Phase 1: 外圈优先轨道密度分配

### 问题
- 原系统：除近轨道和开路轨道外，每个轨道只分配1个单位
- 结果：外层轨道周长大，单位稀疏，视野出现漏洞

### 解决方案
**外圈优先密度分配算法**

1. **基础公式**
   ```python
   # 方形轨道周长
   circumference = 8 × radius
   
   # 所需单位数（保证80%视野重叠）
   required = ceil(circumference / (vision_diameter × 0.8))
   
   # 每层轨道分配的单位数
   units_per_lane = ceil(required / total_units) if total > required else 1
   ```

2. **分配策略**
   - 先铺开：让外层轨道先达到最小可用密度
   - 再加密：按周长比例分配剩余单位
   - 保证外圈密度 ≥ 内圈密度

3. **Phase offset**
   - 同一半径多个单位通过phase offset错开位置
   - 分布在方形轨道的4个角点
   - 公式：`phase_offset = (unit_index_in_lane × 4) / units_per_lane`

### 关键代码位置
- `_lightning_required_units_for_coverage()` (line ~6215): 计算所需单位数
- `_lightning_assign_orbit_lanes_dynamic()` (line ~6225): 动态分配轨道
- `_lightning_orbit_waypoint()` (line ~6843): 生成带phase offset的航点

### 测试覆盖
- `test_phase1_orbit_density_allocation`: 验证外圈密度 ≥ 内圈密度

---

## Phase 2: Core规避 + 工人肉盾行为

### Core象限规避

**问题**: Core巡逻时可能直接走向敌人

**解决方案**:
1. 将地图划分为4个象限（基于Core位置）
2. 检测每个象限内的敌方战斗单位
3. Core跳过有敌人的象限，选择安全方向

**关键代码**:
- `_lightning_patrol_waypoint()` (line ~6079): Core巡逻航点生成
- 敌人象限检测逻辑 (line ~6090+)

### 工人肉盾行为

**威胁分级**:
- **NEAR** (≤6格): 所有空闲工人回近轨道当肉盾
- **MID** (≤20格): 空闲工人回近轨道准备

**行为逻辑**:
```python
if nearest_enemy_dist <= near_threat_radius or nearest_enemy_dist <= mid_threat_radius:
    # 空手工人立即回防近轨道（r=5）
    if current_dist > near_orbit_radius + 2:
        move_toward_core("worker_meatshield_defend")
    else:
        hold_position("worker_meatshield_hold")
```

**关键代码**:
- `_choose_workers()` (line ~3151): 工人选择逻辑，增加威胁检测优先级

### 测试覆盖
- `test_phase2_core_avoidance`: Core规避敌人象限
- `test_phase2_worker_meatshield_NEAR`: NEAR威胁时工人回防

---

## Phase 3: 游侠分层防御 + 开路战术

### 分层防御机制

基于敌方深入程度的三级响应：

#### 1. NEAR威胁 (≤6格)
**响应**: 全员回防
- 所有游侠退入工人包围圈阻击
- 召回开路游侠勤王（沿途绕过敌人）
- 工人回近轨道当肉盾

#### 2. MID威胁 (6-20格)
**响应**: 游侠集结围攻
- 所有中轨游侠集结到威胁位置
- 保持射程（2-3格）狙击
- 工人回近轨道准备

#### 3. FAR威胁 (20-40格)
**响应**: 局部狙击驱离
- 仅视野范围内的中轨游侠参与
- 保持射程不贴脸
- 最远追到外轨道边界（禁止千里追击）

### 开路轨道战术（前4游侠）

**核心职责**:
1. 探索资源
2. 侦察无守卫Core
3. 选择性交战

**威胁响应规则**:
```python
# 见游侠 → 立即逃跑（我方2HP易亏，射程对等无优势）
if enemy_rangers:
    flee_to_core()

# 见多敌 → 逃跑（敌众我寡）
elif len(enemies) > 1:
    flee_to_core()

# 1v1先锋 → 游击（利用射程1-3优势，先锋近战需贴脸）
elif single_vanguard:
    kite_at_distance_2_to_3()

# 无威胁 → 继续巡逻
else:
    patrol_breakthrough_orbit()
```

**选择性交战**:
- 发现无守卫Core → 直接打
- 发现仅1先锋守卫Core → 游击（利用射程优势）
- 发现游侠守卫 → 绕路（拉黑该Core，永不再碰）

### 关键代码位置
- `_lightning_breakthrough_threat_check()` (line ~6420): 开路游侠威胁检测
- `_lightning_find_nearby_unguarded_core()` (line ~6457): 搜索无守卫Core
- `_lightning_should_breakthrough_engage()` (line ~6504): 判定是否交战
- `_lightning_find_nearest_threat()` (line ~6549): 找最近威胁
- `_lightning_intercept_position()` (line ~6567): 计算拦截位置
- `_lightning_kiting_position()` (line ~6596): 计算游击位置
- `_choose_rangers_lightning()` (line ~10203): 游侠主逻辑（重写）

### 测试覆盖
- `test_phase3_ranger_defend_NEAR`: NEAR威胁全员回防
- `test_phase3_ranger_defend_MID`: MID威胁集结围攻
- `test_phase3_breakthrough_flee_on_ranger`: 开路游侠见敌方游侠逃跑
- `test_phase3_breakthrough_kite_single_vanguard`: 开路游侠1v1游击
- `test_phase3_mid_orbit_snipe_FAR`: FAR威胁狙击驱离

---

## 总原则

1. **非必要不进攻** - 除非压倒性优势（如无守卫Core）
2. **资源靠采集，不掠夺** - 经济优先，不冒险抢夺
3. **禁止千里追击** - 追击边界700格（开路轨道外缘）

---

## 决策计数器（新增）

用于监控各战术分支的触发频率：

```python
# Phase 1
"orbit:outer_priority_allocation"

# Phase 2
"worker:meatshield_defend"
"worker:meatshield_hold"
"core:patrol_avoid_quadrant"

# Phase 3
"ranger:defend_NEAR"
"ranger:defend_MID"
"mid_orbit:snipe_FAR"
"mid_orbit:patrol"
"breakthrough:flee"
"breakthrough:kite"
"breakthrough:shoot"
"breakthrough:approach"
"breakthrough:patrol"
```

---

## 部署建议

### 1. 渐进式验证
```bash
# 语法检查
python3 -m py_compile arena_hero_strategy.py

# 单元测试
python3 -m pytest test_four_layer_defense.py -v

# 集成测试（对抗bot）
python3 play_arena_hero.py --mode lightning --opponent basic_bot
```

### 2. 监控重点

**Phase 1监控**:
- 查看不同半径轨道的单位分配数量
- 验证外层轨道密度是否 ≥ 内层

**Phase 2监控**:
- Core是否成功规避敌人象限
- NEAR/MID威胁时工人是否及时回防

**Phase 3监控**:
- 开路游侠遇到不同敌情的响应（逃跑/游击/巡逻）
- MID/FAR威胁时中轨游侠的集结/狙击行为
- 是否出现千里追击（超出700格边界）

### 3. 调优参数

如果出现以下情况，可以调整：

**视野仍有漏洞** → 降低`LIGHTNING_ORBIT_OVERLAP_FACTOR`（当前0.8）
```python
LIGHTNING_ORBIT_OVERLAP_FACTOR = 0.75  # 增加25%重叠
```

**外层单位过多** → 提高overlap factor
```python
LIGHTNING_ORBIT_OVERLAP_FACTOR = 0.85  # 减少15%重叠
```

**开路游侠过于保守** → 调整威胁半径
```python
LIGHTNING_LOCAL_THREAT_RADIUS = 15  # 降低（当前20）
```

**Core规避过于频繁** → 调整象限威胁阈值
```python
# 在_lightning_patrol_waypoint中
enemy_threshold = 2  # 象限内敌人数≥2才规避
```

---

## 风险评估

### 低风险
✓ Phase 1轨道分配（不改变行为，只改变密度）
✓ Phase 2 Core规避（保守策略，只避开威胁）

### 中等风险
⚠ Phase 2工人肉盾（可能影响经济效率）
⚠ Phase 3 FAR威胁狙击（可能误判距离）

### 高风险
⚠⚠ Phase 3游侠主逻辑重写（涉及所有游侠行为）
⚠⚠ 开路游侠战术（新增复杂决策分支）

**缓解措施**:
1. 所有危险操作前已通过语法检查
2. 编写了8个单元测试覆盖关键路径
3. 保留了原有的`_lightning_defense_tier`等核心helper函数
4. 新增逻辑都有fallback到原有巡逻行为

---

## 后续优化方向

1. **动态调整overlap factor** - 根据敌方侦察压力动态收缩/扩张
2. **开路游侠协同** - 4个开路游侠同时发现敌人时协同游击
3. **Core迁移触发** - 当Core巡逻路径被长期封锁时，触发迁移到安全区
4. **先锋轨道密度** - 目前只有1个先锋，未来增加先锋数量时也应用密度算法

---

## 文件变更清单

### 修改文件
- `arena_hero_strategy.py`
  - Phase 1: 新增 `_lightning_required_units_for_coverage()`, `_lightning_assign_orbit_lanes_dynamic()`, 修改 `_lightning_orbit_waypoint()`
  - Phase 2: 修改 `_lightning_patrol_waypoint()`, `_choose_workers()`
  - Phase 3: 新增 `_lightning_breakthrough_threat_check()`, 重写 `_choose_rangers_lightning()`

### 新增文件
- `test_four_layer_defense.py` - 综合测试套件
- `FOUR_LAYER_DEFENSE_IMPLEMENTATION.md` - 本文档

---

## 结论

本次实施完整实现了四层轨道职责和分层防御系统，解决了原有战术的三大痛点：

1. ✅ 轨道密度不合理 → 外圈优先密度分配
2. ✅ Core直冲敌人 → 象限规避 + 工人肉盾
3. ✅ 游侠无层次应敌 → 三级分层防御 + 开路战术

系统已通过语法检查和单元测试，建议先在测试环境验证，观察决策计数器，确认各战术分支按预期触发后再部署到生产环境。
