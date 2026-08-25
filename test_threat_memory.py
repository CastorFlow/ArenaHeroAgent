#!/usr/bin/env python3
"""威胁记忆 + 绕行 + Core 退避持续化 + 45°移动靶转直攻 回归测试。

覆盖本次修复的三个根因：
1. 工人逃出敌方视野后不再沿巡逻一头扎回威胁格（recent_threat_cells 软障碍）。
2. Core 退避三舍在威胁记忆期内维持 evade，不因敌人离视野立即恢复巡逻。
3. 45°偷袭遇移动靶改正向最远射程；静止靶仍 45°优先。
4. ≤4 游侠按"2 预瞄优先 + 剩余突击"编组（2/3/4 人）。
"""

import unittest
from uuid import UUID

from arena_hero import CoreState, CoreView, MoveAction, ShootAction, UnitType, UnitView
from arena_hero_strategy import (
    THREAT_MEMORY_TICKS,
    THREAT_CELL_PATROL_PENALTY,
    ENEMY_MOBILE_SPAN_CELLS,
    ENEMY_MOBILE_SPAN_TICKS,
    TacticMemory,
    SmartTactic,
    MovementPlanner,
    _destination,
    _distance,
)
from test_arena_hero_tactic import (
    enemy_ranger,
    enemy_vanguard,
    make_turn,
    core,
    ranger,
    worker,
)


class ThreatMemoryTests(unittest.TestCase):
    def test_observe_records_combat_threat_cells(self) -> None:
        # 见敌方先锋/游侠 → 其当前格写入 recent_threat_cells，到期 tick = tick+30。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            tick=10, own_core=core((600, 600)),
            units=(ranger((610, 600)),),
            enemies=(enemy_vanguard((615, 600)), enemy_ranger((600, 610))),
        )
        tactic.memory.observe(turn)
        self.assertIn((615, 600), memory.recent_threat_cells)
        self.assertIn((600, 610), memory.recent_threat_cells)
        self.assertEqual(memory.recent_threat_cells[(615, 600)], 10 + THREAT_MEMORY_TICKS)

    def test_observe_ignores_worker_and_core_as_threat(self) -> None:
        # 工人/Core 无攻击力不计入威胁记忆。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        from test_arena_hero_tactic import enemy_worker, enemy_core
        turn, _ = make_turn(
            tick=10, own_core=core((600, 600)),
            units=(ranger((610, 600)),),
            enemies=(enemy_worker((615, 600)), enemy_core((620, 600))),
        )
        tactic.memory.observe(turn)
        self.assertEqual(memory.recent_threat_cells, {})

    def test_threat_memory_expires_after_window(self) -> None:
        # 到期 tick 后 observe 清掉过期项。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.recent_threat_cells[(700, 700)] = 5  # 已过期（tick 10 > 5）
        turn, _ = make_turn(
            tick=10, own_core=core((600, 600)),
            units=(ranger((610, 600)),),
        )
        tactic.memory.observe(turn)
        self.assertNotIn((700, 700), memory.recent_threat_cells)

    def test_threat_memory_clears_when_own_unit_occupies_cell(self) -> None:
        # 我方单位站上威胁格 → 威胁已解除，清掉，避免自家脚下被当软障碍。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.recent_threat_cells[(610, 600)] = 999  # 未过期
        turn, _ = make_turn(
            tick=10, own_core=core((600, 600)),
            units=(ranger((610, 600)),),  # 我方游侠站上该格
        )
        tactic.memory.observe(turn)
        self.assertNotIn((610, 600), memory.recent_threat_cells)

    def test_flee_score_penalizes_threat_memory_cell(self) -> None:
        # 工人逃命时，威胁记忆格方向应被重罚（+50），不会被选为逃跑方向。
        # 构造：敌先锋正北 (600,593) → 南/东/西三向离敌均 8 格、exits/cd 相同，
        # flee_from_combat 的 DIRECTION_RANK 决胜选 RIGHT(rank1)。把 RIGHT 邻格
        # (601,600) 设为威胁记忆格 → +50 把 RIGHT 排除 → 改走 DOWN/LEFT。
        # 关键：威胁格必须是"无威胁时本会被选中的方向"，惩罚才有可观察效果。
        # 原 bug 把威胁放 (600,603)(四邻外)→惩罚永不命中、测试空过。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.recent_threat_cells[(601, 600)] = 999  # 东向邻格=威胁记忆格
        wk = worker(UUID(int=0x1001), (600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((600, 600)),
            units=(wk,),
            enemies=(enemy_vanguard((600, 593)),),  # 敌正北 → RIGHT 是自然逃向
        )
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        moved = planner.flee_from_combat(
            turn.workers[0],
            [next(e for e in turn.visible_enemies if isinstance(e, UnitView))],
            (600, 600), "worker_combat_flee",
        )
        self.assertTrue(moved, decisions)
        move_line = next(d for d in decisions if " move " in d)
        direction = move_line.split()[move_line.split().index("move") + 1]
        self.assertNotEqual(direction, "RIGHT",
                            f"东被威胁惩罚排除后仍朝东逃 {move_line}")
        # 对照：无威胁记忆时 RIGHT 是 DIRECTION_RANK 决胜的逃向，应被选中
        # (证明威胁惩罚确实改变了结果)。
        memory2 = TacticMemory()
        turn2, _ = make_turn(
            tick=10, own_core=core((600, 600)),
            units=(worker(UUID(int=0x1001), (600, 600)),),
            enemies=(enemy_vanguard((600, 593)),),
        )
        decisions2: list[str] = []
        MovementPlanner(turn2, memory2, decisions2).flee_from_combat(
            turn2.workers[0],
            [next(e for e in turn2.visible_enemies if isinstance(e, UnitView))],
            (600, 600), "worker_combat_flee",
        )
        ml2 = next(d for d in decisions2 if " move " in d)
        d2 = ml2.split()[ml2.split().index("move") + 1]
        self.assertEqual(d2, "RIGHT",
                         f"无威胁记忆时应朝东逃(证明惩罚改变结果) {ml2}")

    def test_worker_patrol_avoids_threat_memory_cell(self) -> None:
        # 工人朝目标走时，若目标直连下一格是威胁记忆格，威胁惩罚应让单位不直扎进去。
        # 构造：工人 (600,600)，目标 (600,610)，路径直连向南；威胁记忆格 (600,601) 是
        # 南向下一格 → 评分 +8 让南向失分，单位改走其他方向。
        # 注：首 tick heading=None 时四向 DIRECTION_RANK 决胜可能选 UP(背向)——这是绕行
        # 的第一步,后续 tick 折返朝 goal(由 test_worker_patrol_bypass_does_not_detour_
        # away_from_goal 多 tick 验证)。这里只验证"不直扎威胁格"。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.recent_threat_cells[(600, 601)] = 999  # 南向下一格是威胁记忆格
        wk = worker(UUID(int=0x1002), (600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((600, 600)),
            units=(wk,),
        )
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        controllable_wk = turn.workers[0]
        tactic._lightning_step_toward(turn, planner, controllable_wk, (600, 610), "test_patrol")
        move_lines = [d for d in decisions if " move " in d]
        self.assertTrue(move_lines, f"应移动;决策={decisions}")
        line = move_lines[0]
        parts = line.split()
        direction = parts[parts.index("move") + 1]
        # 不应直接往南扎进威胁记忆格（600,601）。
        self.assertNotEqual(direction, "DOWN",
                            f"巡逻直接扎进威胁记忆格 {line}")

    def test_worker_patrol_bypass_does_not_detour_away_from_goal(self) -> None:
        # 回归(Finding 3)：威胁格挡直连路径时，绕行不应让单位背向 goal 一路远去。
        # 工人 (600,600) 朝南 goal (600,610)，南向第一格 (600,601) 是威胁记忆格。
        # 旧实现：heading 惯性 + DIRECTION_RANK 让单位一路向北背向走几十格、距 goal
        # 越来越远(失去绕行意义)。修复后应侧向绕 1 格后折返朝 goal，能到达 goal 的 y 行。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.recent_threat_cells[(600, 601)] = 999
        wk_id = UUID(int=0x1003)
        cur = (600, 600)
        positions = [cur]
        from arena_hero_strategy import DIRECTION_ORDER, _destination
        for tick in range(10, 45):
            turn, _ = make_turn(
                tick=tick, own_core=core((600, 600)),
                units=(worker(wk_id, cur),),
            )
            decisions: list[str] = []
            planner = MovementPlanner(turn, memory, decisions)
            moved = tactic._lightning_step_toward(
                turn, planner, turn.workers[0], (600, 610), "test_patrol"
            )
            md = [d for d in decisions if " move " in d]
            if md and moved:
                import re
                m = re.search(r"to=\((-?\d+), (-?\d+)\)", md[0])
                new = (int(m.group(1)), int(m.group(2)))
                for d in DIRECTION_ORDER:
                    if _destination(cur, d) == new:
                        memory.unit_headings[str(wk_id)] = d
                        break
            else:
                new = cur
            positions.append(new)
            cur = new
            memory.recent_positions.setdefault(str(wk_id), []).append(new)
            if len(memory.recent_positions[str(wk_id)]) > 16:
                memory.recent_positions[str(wk_id)] = memory.recent_positions[str(wk_id)][-16:]
            memory.visited[new] = memory.visited.get(new, 0) + 1
        # 关键回归点：不应一路向北背向(goal 在南)。修复前末尾在 (600,575) 一带 y<600。
        # 修复后绕行折返，能越过威胁到达 goal 的 y 行(610) 或更南(继续巡逻)。
        final = positions[-1]
        self.assertGreater(
            final[1], 605,
            f"绕行未折返朝 goal,末尾 {final} 仍卡在威胁北侧;轨迹末尾 {positions[-8:]}"
        )
        # 且绕行偏移有限：x 漂移不超过 3 格(修复前若向东锁死会漂出十几格)。
        self.assertLessEqual(
            abs(final[0] - 600), 4,
            f"绕行横向漂移过大:末尾 {final};轨迹末尾 {positions[-8:]}",
        )


class CoreEvadeThreatMemoryTests(unittest.TestCase):
    def test_core_evade_persists_after_enemy_leaves_vision(self) -> None:
        # 敌方进视野 → 退避 evade。敌方离视野后，recent_threat_cells 仍在期内 →
        # _core_enemy_bias 应继续返回 "evade"（不立即恢复巡逻一头扎回去）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.core_evade_enemies = True
        # 先见敌：observe 写威胁记忆。
        turn1, _ = make_turn(
            tick=10, own_core=core((600, 600)),
            units=(ranger((610, 600)),),
            enemies=(enemy_ranger((605, 600)),),
        )
        tactic.memory.observe(turn1)
        self.assertIn((605, 600), memory.recent_threat_cells)
        # 下一 tick 敌人离视野：visible_enemies 空，但威胁记忆仍在。
        turn2, _ = make_turn(
            tick=11, own_core=core((600, 600)),
            units=(ranger((610, 600)),),
            enemies=(),  # 敌人不可见
        )
        tactic.memory.observe(turn2)
        # 威胁记忆未被清（tick 11 < 10+30）。
        self.assertIn((605, 600), memory.recent_threat_cells)
        self.assertEqual(tactic._core_enemy_bias(turn2), "evade")

    def test_core_evade_clears_after_memory_expires(self) -> None:
        # 威胁记忆老化后 _core_enemy_bias 应回到 "none"（无可见敌人 + 无记忆）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.core_evade_enemies = True
        memory.recent_threat_cells[(605, 600)] = 10  # tick 20 时已过期
        turn, _ = make_turn(
            tick=20, own_core=core((600, 600)),
            units=(ranger((610, 600)),),
            enemies=(),
        )
        tactic.memory.observe(turn)  # 清掉过期项
        self.assertEqual(tactic._core_enemy_bias(turn), "none")

    def test_core_chase_uses_visible_only_not_memory(self) -> None:
        # 趁胜追击只用可见敌人；敌人离视野后即使有威胁记忆也不追（追击风险高）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.core_chase_enemies = True
        memory.core_evade_enemies = False
        memory.recent_threat_cells[(605, 600)] = 999  # 记忆在
        turn, _ = make_turn(
            tick=11, own_core=core((600, 600)),
            units=(ranger((610, 600)),),
            enemies=(),  # 无可见敌人
        )
        tactic.memory.observe(turn)
        # 无可见敌人 + 仅记忆 → chase 不生效（退避才用记忆）。
        self.assertEqual(tactic._core_enemy_bias(turn), "none")


class EnemyMobileAndAssaultTests(unittest.TestCase):
    def test_enemy_is_mobile_true_for_moving_target(self) -> None:
        # 近 3 帧位置跨度 2 格 → mobile=True。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        enemy = enemy_ranger((600, 600))
        # 写轨迹：3 帧跨度 2 格。
        memory.enemy_trails[str(enemy.id)] = [(600, 600), (601, 600), (602, 600)]
        self.assertTrue(tactic._enemy_is_mobile(enemy))

    def test_enemy_is_mobile_false_for_stationary_target(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        enemy = enemy_ranger((600, 600))
        memory.enemy_trails[str(enemy.id)] = [(600, 600), (600, 600), (600, 600)]
        self.assertFalse(tactic._enemy_is_mobile(enemy))

    def test_enemy_is_mobile_false_short_trail(self) -> None:
        # 轨迹不足 → 保守 False（继续 45°）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        enemy = enemy_ranger((600, 600))
        memory.enemy_trails[str(enemy.id)] = [(600, 600)]
        self.assertFalse(tactic._enemy_is_mobile(enemy))

    def test_assault_fire_cell_straight_for_mobile_target(self) -> None:
        # 移动靶 → fire_cell 选正向最远格（连续邻格），不选 45°斜角。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        target = enemy_ranger((600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(ranger((570, 590)),),
            enemies=(target,),
        )
        planner = MovementPlanner(turn, memory, [])
        cell = tactic._ranger_assault_fire_cell(
            turn, target, planner, [ranger((570, 590))], mobile=True
        )
        self.assertIsNotNone(cell)
        # 正向最远：dx==3,dy==0 或 dx==0,dy==3。
        dx = abs(cell[0] - target.position[0])
        dy = abs(cell[1] - target.position[1])
        self.assertTrue(
            (dx == 3 and dy == 0) or (dx == 0 and dy == 3),
            f"移动靶应选正向最远格,实际 {cell} (dx={dx},dy={dy})",
        )

    def test_assault_fire_cell_diagonal_for_stationary_target(self) -> None:
        # 静止靶 → 45°斜角优先（盲区偷袭）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        target = enemy_ranger((600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(ranger((570, 590)),),
            enemies=(target,),
        )
        planner = MovementPlanner(turn, memory, [])
        cell = tactic._ranger_assault_fire_cell(
            turn, target, planner, [ranger((570, 590))], mobile=False
        )
        self.assertIsNotNone(cell)
        dx = abs(cell[0] - target.position[0])
        dy = abs(cell[1] - target.position[1])
        # 静止优先 45°（dx==3,dy==3）；若无 45° 可达格才退到正向。
        self.assertTrue(
            (dx == 3 and dy == 3) or (dx == 3 and dy == 0) or (dx == 0 and dy == 3),
            f"静止靶 fire_cell 异常 {cell}",
        )

    def test_standoff_relay_cell_cardinal_for_mobile_target(self) -> None:
        # 回归(Finding 1)：mobile=True 时 _standoff_relay_cell 应返回严格正向格
        # (dx=3,dy=0 或 dx=0,dy=3)，不返回 (3,3) 对角格。旧 straight_far 过滤器
        # max(|dx|,|dy|)==3 把 (3,3) 对角格也算"正向"，使 mobile 标志对 relay 路径失效。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        target = enemy_ranger((600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(ranger((570, 590)),),
            enemies=(target,),
        )
        planner = MovementPlanner(turn, memory, [])
        cell = tactic._standoff_relay_cell(turn, target, planner, mobile=True)
        self.assertIsNotNone(cell)
        dx = abs(cell[0] - target.position[0])
        dy = abs(cell[1] - target.position[1])
        self.assertTrue(
            (dx == 3 and dy == 0) or (dx == 0 and dy == 3),
            f"移动靶 relay_cell 应严格正向,实际 {cell} (dx={dx},dy={dy})",
        )

    def test_execute_ranger_direct_assault_uses_mobile_for_moving_target(self) -> None:
        # 回归(Finding 4)：_execute_ranger_direct_assault 对移动靶应选正向 fire_cell
        # （与 _ranger_assault_plan 一致）。漏传 mobile= 默认 False → 走 45°漂移格。
        # 构造：游侠远在 (570,590)，敌游侠移动中(轨迹跨度 2)，direct_assault 选正向格。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        target = enemy_ranger((600, 600))
        memory.enemy_trails[str(target.id)] = [(600, 600), (601, 600), (602, 600)]
        r1 = ranger((570, 590), UUID(int=0xA010))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(r1,),
            enemies=(target,),
        )
        decisions: list[str] = []
        tactic._execute_ranger_direct_assault(turn, MovementPlanner(turn, memory, decisions), turn.rangers[0], target, decisions)
        advance_lines = [d for d in decisions if "direct_assault_advance" in d]
        self.assertTrue(advance_lines, f"应推进;决策={decisions}")
        # 解析 fire_cell=(x, y)。
        import re
        m = re.search(r"fire_cell=\((-?\d+), (-?\d+)\)", advance_lines[0])
        self.assertIsNotNone(m, advance_lines[0])
        cell = (int(m.group(1)), int(m.group(2)))
        dx = abs(cell[0] - target.position[0])
        dy = abs(cell[1] - target.position[1])
        self.assertTrue(
            (dx == 3 and dy == 0) or (dx == 0 and dy == 3),
            f"移动靶 direct_assault 应选正向 fire_cell,实际 {cell} (dx={dx},dy={dy})",
        )

    def test_assault_plan_with_two_rangers_all_preaim(self) -> None:
        # 2 个游侠 → 编组成立(2 预瞄 + 0 突击)。原 ≥4 时不成立。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((598, 600), UUID(int=0xA001))
        r2 = ranger((602, 600), UUID(int=0xA002))
        target = enemy_ranger((600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(r1, r2),
            enemies=(target,),
        )
        planner = MovementPlanner(turn, memory, [])
        plan = tactic._ranger_assault_plan(turn, planner, set(), {})
        self.assertIsNotNone(plan, "2 游侠应能成立编组")
        assert plan is not None
        self.assertEqual(len(plan.preaim_ids), 2)
        self.assertEqual(len(plan.assault_ids), 0)

    def test_assault_plan_with_three_rangers_two_preaim_one_assault(self) -> None:
        # 3 个游侠 → 2 预瞄 + 1 突击。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((598, 600), UUID(int=0xA001))
        r2 = ranger((602, 600), UUID(int=0xA002))
        r3 = ranger((600, 598), UUID(int=0xA003))
        target = enemy_ranger((600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(r1, r2, r3),
            enemies=(target,),
        )
        planner = MovementPlanner(turn, memory, [])
        plan = tactic._ranger_assault_plan(turn, planner, set(), {})
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan.preaim_ids), 2)
        self.assertEqual(len(plan.assault_ids), 1)


class RangerPatrolStationaryTimeoutTests(unittest.TestCase):
    def test_stationary_ranger_forced_into_escape(self) -> None:
        # 游侠连续 RANGER_STATIONARY_LIMIT 个 tick 位置不变 → 强制进逃生 +
        # patrol_escape_forced 计数。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.core_orbit_radius = 550  # 启用巡逻
        r_unit = ranger((610, 610), UUID(int=0xB040))
        # 预置 last_position_tick 为很久以前 → 触发静止超时。
        memory.last_position_tick[str(r_unit.id)] = 10
        turn, _ = make_turn(
            tick=10 + 16,  # 距上次移动 ≥ 16
            own_core=core((600, 600)),
            units=(r_unit,),
        )
        summary = tactic.choose_actions(turn)
        self.assertGreater(
            memory.decision_totals.get("ranger:patrol_escape_forced", 0), 0,
            f"应触发 patrol_escape_forced;决策={summary.decisions}",
        )
        self.assertGreater(
            memory.lightning_unit_escape_until.get(str(r_unit.id), 0), turn.tick,
            "应写入 escape_until",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
