# 基于周长的动态轨道分配方案

## 设计目标

1. **最大化覆盖面积**：优先铺开外层轨道
2. **均匀分布**：每层单位数量与周长成正比
3. **防止盲区**：单位间距 ≤ 视野直径 × 1.2
4. **动态调整**：根据威胁/资源方向调整密度

## 核心公式

### 基础参数
- **方环周长**：C(r) = 8r（四边各2r）
- **理想间距**：
  - 先锋：5格（视野4，1.25倍）
  - 游侠：6格（视野5，1.2倍）
  - 工人：4格（视野3，1.33倍）
- **单层容量**：cap(r) = C(r) / interval = 8r / interval

### 分配算法

**Phase 1: 确定轨道数量和半径**
```python
# 从inner_radius开始，每层间隔gap
orbits = []
r = inner_radius
while r <= max_radius:
    orbits.append(r)
    r += gap
```

**Phase 2: 按周长比例分配单位**
```python
# 计算每层容量（周长/间距）
capacities = [max(2, min(8, (8 * r) // ideal_interval)) for r in orbits]

# 计算总容量和比例
total_capacity = sum(capacities)
distribution = []

for i, (r, cap) in enumerate(zip(orbits, capacities)):
    # 按容量比例分配
    allocated = round(unit_count * cap / total_capacity)
    # 限制在2~cap之间
    allocated = max(2, min(cap, allocated))
    distribution.append((r, allocated))
```

**Phase 3: 处理余数**
```python
assigned = sum(count for _, count in distribution)
remaining = unit_count - assigned

# 余数优先分配给外层（覆盖面积大）
for i in range(len(distribution) - 1, -1, -1):
    if remaining <= 0:
        break
    r, count = distribution[i]
    cap = capacities[i]
    if count < cap:
        extra = min(remaining, cap - count)
        distribution[i] = (r, count + extra)
        remaining -= extra
```

## 具体案例

### 案例1：8个游侠
- inner_radius = 9（先锋1个，占r=5）
- gap = 5
- ideal_interval = 6

**轨道列表**：
- r=9: 周长72, 容量12, 分配3个
- r=14: 周长112, 容量18, 分配4个
- r=19: 周长152, 容量25→8(上限), 分配1个（余数）

**结果**：`[(9,3), (14,4), (19,1)]`

### 案例2：20个游侠
- 同上参数

**轨道列表**：
- r=9: 容量12, 分配3个
- r=14: 容量18→8, 分配5个
- r=19: 容量25→8, 分配6个
- r=24: 容量32→8, 分配6个

**结果**：`[(9,3), (14,5), (19,6), (24,6)]`

## 与电子排布的对比

| 维度 | 电子排布 | 周长分配 |
|------|----------|----------|
| 每层容量 | 2n² (2,8,18,32) | 8r/interval (线性) |
| 增长速度 | 平方增长 | 线性增长 |
| 物理基础 | 量子力学角动量 | 几何周长 |
| 适用场景 | 稳定态原子 | 动态战场探测 |

**为什么周长方案更适合游戏**：
1. **地图非对称**：资源/敌人有方向，不是中心对称
2. **动态响应**：需要快速集结，不是静态填充
3. **覆盖优先**：外层半径大，单位多才能覆盖
4. **视野约束**：间距由视野决定，不是量子数

## 效率评估

### 探测覆盖率
- **电子排布**（前3层2+8+18=28个游侠）：
  - r=5: 2个，间距20格（盲区巨大）
  - r=10: 8个，间距10格（勉强覆盖）
  - r=15: 18个，间距6.7格（合理）

- **周长分配**（前3层约20个游侠）：
  - r=9: 3个，间距24格（需调整）
  - r=14: 5个，间距22格
  - r=19: 6个，间距25格
  - r=24: 6个，间距32格

**调整**：将 `min_units_per_orbit` 从2提到3-4，配合周长比例分配。

### 响应速度
- **电子排布**：固定层数，外层必须等前两层填满（28个）
- **周长分配**：外层优先，第5个游侠就能上r=19

## 实施步骤

1. **替换分配函数**：
   ```python
   def _lightning_calculate_circumference_based_orbits(...) -> list[tuple[int, int]]:
       # 新算法实现
   ```

2. **调整参数**：
   ```python
   LIGHTNING_IDEAL_INTERVAL = {
       UnitType.VANGUARD: 5,
       UnitType.RANGER: 6,
       UnitType.WORKER: 4,
   }
   ```

3. **保留现有phase系统**：
   - 同一半径的单位仍用 `phase_offset` 错开
   - 轨道waypoint逻辑不变

4. **测试场景**：
   - 2个游侠 → 应集中在r=9一层
   - 10个游侠 → 应分布在r=9,14,19三层
   - 20个游侠 → 应分布在r=9,14,19,24四层
