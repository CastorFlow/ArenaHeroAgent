# Arena Hero 日志系统修复总结

**修复时间**: 2026-08-10 17:34
**部署位置**: vps168

---

## 已完成的修复

### 1. ✅ 修复 `AttributeError: aggress_heal_rotations` Bug

**问题**: `TacticMemory` 类缺少 `aggress_heal_rotations` 字段定义

**影响**: tick 83779 策略代码崩溃，导致游侠#2 站桩被敌方先锋击杀

**修复**: 在 `arena_hero_strategy.py:455` 添加字段定义

```python
aggress_heal_rotations: dict[str, int] = field(default_factory=dict)
```

**验证**:
- ✅ 服务成功重启
- ✅ tick 84130-84131 自动重新加载策略
- ✅ 无 AttributeError 崩溃

---

### 2. ✅ 移除决策日志截断限制

**问题**: `arena_hero_tactic.py:241` 只显示前 8 个决策，20 人口时丢失 60% 信息

**影响**: 看不到大部分战斗单位的决策，无法追踪游侠/先锋行为

**修复**: 移除 `[:8]` 切片限制

```python
# 修复前
decision_text = " | ".join(summary.decisions[:8]) or "wait"

# 修复后
decision_text = " | ".join(summary.decisions) or "wait"
```

**验证**:
- ✅ systemd 日志显示所有单位的决策
- ✅ 可以看到游侠的 `mid_orbit_patrol lane=X` 信息
- ✅ 可以看到先锋的 `lightning_vanguard_orbit` 信息

---

## 游侠损失事件完整复盘

### 事件时间线

**tick 83777**: 发现敌方工人在 [464, 45]，生命 1

**tick 83778**: 发现敌方先锋在 [465, 43]，生命 4

**tick 83779**:
- ❌ 策略代码因 `AttributeError: aggress_heal_rotations` 崩溃
- ❌ 没有提交任何行动计划
- ⚠️ 游侠#2 (生命 2) 在 [465, 42] 无法行动
- 💥 敌方先锋攻击游侠#2
- ☠️ 游侠#2 受到 1 点伤害并阵亡
- ✅ **事件日志正确记录**: `"游侠#2 在 [465, 42] 受到 1 点伤害并阵亡（遭受攻击）"`

**tick 83780**:
- 敌情解除（敌方撤离）
- 资源容量 100→95（确认损失 1 个游侠）

**tick 83782-83783**:
- Core 生产新游侠#7
- 花费 12 资源，人口恢复至 20

### 根本原因

**直接原因**: 代码崩溃导致单位无法执行规避动作

**深层原因**: 代码在使用 `self.memory.aggress_heal_rotations` 前没有初始化该字段

**教训**:
1. 所有 `TacticMemory` 字段都应该在类定义中声明并初始化
2. 代码崩溃比敌人攻击更危险——一次崩溃可能导致全部单位站桩被打
3. 需要监控策略运行时错误，及时发现并修复

---

## 日志系统评估

### ✅ 工作正常的部分

1. **事件日志系统** (`arena_hero_events_zh.jsonl`)
   - 完整记录所有关键事件
   - 单位阵亡事件正确记录（`UNIT_DAMAGED` with `hp: 0`）
   - 包含位置、伤害、原因等详细信息
   - 结构化 JSON 格式，易于解析

2. **遥测日志系统** (`arena_hero_telemetry.jsonl`)
   - 记录完整的策略状态
   - 包含内存持久化的所有字段
   - 可用于事后分析和回放

3. **工人决策日志**
   - 包含 `lightning_worker_orbit` 轨道信息
   - 显示目标位置和移动原因

### ⚠️ 已改进的部分

1. **决策日志完整性**
   - **修复前**: 只显示 8 个决策，战斗单位不可见
   - **修复后**: 显示所有单位的决策，包括游侠和先锋的轨道信息

2. **战斗单位轨道信息**
   - **现状**: 游侠显示 `mid_orbit_patrol lane=X`
   - **现状**: 先锋显示 `lightning_vanguard_orbit`
   - **改进**: reason 字段包含了轨道分配信息

### 📊 示例日志输出（修复后）

```
tick=84130 accepted=True resources=53/100 population=20 enemies=0
unit_actions=20 core_action=False
events={'UNIT_MOVE_SUCCEEDED': 20, 'CORE_MOVE_SUCCEEDED': 1}
decisions=
  worker:21317ede move DOWN to=(512, -673) goal=(524, -650) reason=return_cargo |
  worker:25fb2b2c move UP to=(440, -647) goal=(524, -650) reason=return_cargo |
  ... (共 12 个工人) ...
  vanguard:bf599a0c move UP to=(521, -645) goal=(529, -645) reason=lightning_vanguard_orbit |
  vanguard:e55a7a61 move RIGHT to=(390, -655) goal=(519, -655) reason=lightning_vanguard_orbit |
  ranger:05980142 move RIGHT to=(534, -649) goal=(538, -636) reason=mid_orbit_patrol |
  ranger:05980142 mid_orbit_patrol lane=0 |
  ranger:0b2d5ec7 move DOWN to=(502, -682) goal=(510, -664) reason=mid_orbit_patrol |
  ranger:0b2d5ec7 mid_orbit_patrol lane=1 |
  ... (共 6 个游侠) ...
  lightning patrol waypoint=(650, -650) phase=1 |
  core logistics_hold nearest_cargo=8 radius=8
```

**改进点**:
- ✅ 所有 20 个单位的决策都可见
- ✅ 游侠显示 `mid_orbit_patrol lane=X`
- ✅ 先锋显示 `lightning_vanguard_orbit`
- ✅ 包含闪电模式的巡逻航点和相位信息
- ✅ Core 的后勤状态也有记录

---

## 验证结果

### 部署验证

```bash
# 1. 部署修复后的代码
scp -P 9393 arena_hero_strategy.py arena_hero_tactic.py root@vps168:/root/arenahero/
✅ 成功

# 2. 重启服务
systemctl restart arena-hero-agent
✅ 服务正常启动

# 3. 检查状态
systemctl status arena-hero-agent
✅ Active: active (running)
✅ Memory: 50.5M (在限制范围内)
✅ CPU: 468ms (正常)

# 4. 验证日志
✅ tick 84130: strategy_reload_pending=True
✅ tick 84131: strategy_reloaded=True
✅ 所有决策正常记录
✅ 无崩溃错误
```

### 功能验证

- ✅ **不再崩溃**: 无 AttributeError
- ✅ **完整决策日志**: 所有 20 个单位可见
- ✅ **轨道信息可见**: 游侠和先锋的轨道分配已记录
- ✅ **自动重载**: 策略热更新成功

---

## 后续改进建议

### P1 (本周)

1. **在简化日志中添加关键事件摘要**

   建议在每个 tick 的日志中增加重要事件的摘要：
   ```
   tick=83779 ERROR=AttributeError CRITICAL_EVENTS={UNIT_DIED:1 ranger#2@[465,42]}
   ```

2. **添加策略崩溃的上下文信息**

   当策略崩溃时，记录当前危险状态：
   ```python
   if strategy_failed:
       log_crash_context(tick, nearby_enemies, units_at_risk)
   ```

### P2 (未来)

1. **结构化战斗日志**

   单独记录每次战斗的完整过程：
   ```json
   {
     "combat_id": "83779-465-42",
     "tick": 83779,
     "attacker": "enemy_vanguard@[465,43]",
     "defender": "ranger#2@[465,42]",
     "damage": 1,
     "result": "defender_killed",
     "context": "strategy_crashed_no_evasion"
   }
   ```

2. **添加健康检查日志**

   定期记录策略健康状态：
   - 内存使用
   - 决策延迟
   - 异常计数
   - 轨道分配覆盖率

3. **日志可视化工具**

   开发工具从日志重建：
   - 单位移动轨迹
   - 轨道分配时间线
   - 战斗事件地图

---

## 总结

### 核心发现

1. ✅ **日志系统本身是完整的**
   - 事件日志正确记录了游侠阵亡
   - 问题不在日志缺失，而在代码 bug

2. 🐛 **代码质量问题更危险**
   - 一次 AttributeError 导致整回合无响应
   - 比敌人攻击更致命

3. 📝 **日志可读性已改善**
   - 现在可以看到所有单位的决策
   - 轨道信息已经在日志中

### 关键结论

**这次游侠损失不是因为日志不全，而是因为代码 bug 导致策略崩溃。**

日志系统已经完整记录了整个过程，只是简化日志 (`arena_hero.log`) 的可读性不够好。修复后的系统现在可以：

- ✅ 看到所有单位的决策
- ✅ 追踪游侠和先锋的轨道分配
- ✅ 从事件日志追溯单位损失的原因
- ✅ 防止同类 bug 再次导致崩溃

**部署状态**: ✅ 已成功部署到 vps168，服务运行正常
