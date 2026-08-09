# Arena Hero Agent 优化总结

## 修改完成时间
2026-08-10

## 优化目标
1. **资源容量管理**：在资源快满时主动造工人消耗资源，避免溢出浪费
2. **工人采集效率**：途中发现更近资源时动态切换，避免"路过近资源走向远资源"

---

## 核心修改

### 1. 新增人口上限常量（arena_hero_strategy.py:71-73）
```python
ABSOLUTE_MAX_POPULATION = 100
```
- 保持 `LIGHTNING_MAX_POPULATION = 20` 战斗配置不变
- 20-100 人区间只造紧急工人，不造战斗单位
- 战斗力配比（游侠/先锋）不受影响

### 2. 紧急工人逻辑（arena_hero_strategy.py:10044-10055）
在 `_select_spawn()` 开头添加：
```python
# 资源达到容量 80% 时优先造工人
capacity = turn.resource_capacity
urgency_threshold = int(capacity * 0.8)
if (
    current_population < ABSOLUTE_MAX_POPULATION
    and projected_resources >= urgency_threshold
    and projected_resources >= worker_cost
):
    return UnitType.WORKER
```

**触发条件**：
- 未达 100 人总上限
- 资源 ≥ 容量 × 0.8（留 20% 缓冲）
- 买得起工人

**优先级**：紧急工人 > 固定产兵阶梯

### 3. 工人动态切换逻辑（arena_hero_strategy.py:3511-3531）
在 `_choose_workers()` 资源分配段添加：
```python
# 动态切换到更近资源（至少近 2 格）
current_distance = _distance(worker.position, goal.position)
switch_threshold = 2
closer_resources = [
    pos for pos in available_resources
    if pos != goal.position
    and _distance(worker.position, pos) < current_distance - switch_threshold
]

if closer_resources:
    new_target = min(closer_resources, key=lambda pos: _distance(worker.position, pos))
    available_resources.add(goal.position)  # 释放旧目标
    available_resources.discard(new_target)
    self.memory.set_worker_goal(worker, "visible_resource", new_target, turn.tick)
```

**关键设计**：
- **2 格切换阈值**：平衡灵活性与稳定性，避免频繁摇摆
- **旧目标释放**：切换时将旧资源放回池中，供其他工人选择
- **决策日志**：记录 `switch_to_closer_resource` 方便观察

---

## 新增测试用例（test_arena_hero_tactic.py:1135-1239）

### 资源容量测试（3 个）
1. `test_emergency_worker_at_80_percent_capacity`
   - 资源达到 80% 容量时优先造工人
   
2. `test_emergency_worker_respects_100_cap`
   - pop=100 时停止产兵
   
3. `test_regular_build_order_under_20_when_capacity_ok`
   - 资源未达 80% 时按产兵阶梯造兵

### 工人动态切换测试（3 个）
4. `test_worker_switches_to_closer_resource`
   - 发现近 2 格以上资源时切换
   
5. `test_worker_no_switch_within_threshold`
   - 仅近 1 格时不切换（未达阈值）
   
6. `test_worker_releases_old_target_on_switch`
   - 切换时释放旧目标供其他工人选择

---

## 验证步骤

### 1. 运行测试套件
```bash
python -m pytest test_arena_hero_tactic.py -v
```
预期：所有测试通过，包括 6 个新测试。

### 2. 实战观察
```bash
python arena_hero_tactic.py --max-turns 100
```

**关注指标**：

#### 资源容量管理
- 查看 `arena_hero_telemetry.jsonl`：
  ```bash
  grep "spawn WORKER" arena_hero_telemetry.jsonl | jq '.resources, .resource_capacity'
  ```
  预期：造工人时 `resources/capacity` 接近 0.8

- 检查是否还有满仓：
  ```bash
  jq 'select(.resources >= .resource_capacity)' arena_hero_telemetry.jsonl
  ```
  预期：基本没有（偶尔边界情况可能出现）

- 人数分布：
  ```bash
  jq '.population' arena_hero_telemetry.jsonl | sort -n | uniq -c
  ```
  预期：常规游戏在 20 人以内，资源富足时看到 20-100 区间

#### 工人切换行为
- 查看切换决策：
  ```bash
  grep "switch_to_closer_resource" arena_hero_event_log.txt
  ```
  预期：有切换事件，但不应每 tick 都切换

- 验证旧问题消失：
  观察工人是否还会"明明路过近资源却走向远资源"
  预期：这种情况应大幅减少

---

## 预期效果

### 资源容量管理
- **避免溢出**：资源达到 80% 时主动造工人，基本杜绝满仓
- **容量扩张**：工人数量增加 → 容量增加（`max(10, pop*5)`）→ 可容纳更多资源
- **正反馈**：资源多 → 造工人 → 容量大 → 采集不溢出 → 经济更强

### 工人采集效率
- **路径优化**：途中发现更近资源立即切换，减少无效路程
- **采集速度**：工人平均到达资源的时间缩短
- **资源利用**：不会因为工人"固执"走远路而导致近处资源被浪费

### 战斗力不受影响
- 20 人战斗配置保持不变（游侠/先锋/工人比例照旧）
- 额外工人（20-100）纯粹用于资源管理，不稀释战斗单位
- 可逐步观察：先验证 20 人表现，再看资源压力下是否自然扩张到 20+

---

## 风险与缓解

### 1. 紧急造工人可能造太多
**风险**：资源富足时可能造出大量工人（20-100 区间）
**缓解**：
- 80% 阈值避免过早触发
- 只在确实快溢出时造，不是主动扩张
- 工人成本随人数增长，自然减缓扩张速度

### 2. 动态切换可能摇摆
**风险**：频繁切换导致工人来回走
**缓解**：2 格切换阈值，确保有明显收益才切换

### 3. 游戏平衡
**风险**：100 人可能改变经济上限
**观察**：容量公式 `pop*5` + 递增成本自然限制，需实战观察

---

## 决策日志关键词（用于调试）

在 `arena_hero_telemetry.jsonl` 和 `arena_hero_event_log.txt` 中查找：

- `core spawn WORKER` + 高 `resources/capacity` → 紧急工人触发
- `worker:xxx switch_to_closer_resource old=(x,y)(d=N) new=(a,b)(d=M)` → 动态切换
- `worker:switch_to_closer` 计数器 → 切换频率统计

---

## 如有问题

1. **测试失败**：检查是否是新逻辑与现有假设冲突
2. **工人频繁切换**：降低阈值（当前 2 格可能太激进，可改成 3 格）
3. **资源仍溢出**：降低触发阈值（当前 80% 可能太晚，可改成 70%）
4. **造太多工人**：提高触发阈值（当前 80% 可能太早，可改成 90%）

所有参数都在代码中硬编码为常量，方便调整。
