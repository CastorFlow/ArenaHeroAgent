#!/usr/bin/env python3
"""攻击系统重写 v3 回归测试(2026-08-28):

三套并行机制 + 被动优先:
1. 被动 A:敌满血游侠进射程(互覆盖)→ 按血量+攻击力判换血(passive_trade)/逃(passive_flee)。
2. 单杀舞步(第五节表,无状态):dist1+hp>=3→后退;dist1+hp<=2→预瞄撤退格;
   dist2+靠近→预瞄中间格;dist2+hp<=2+远离→预瞄撤退格。dist3 不触发。
3. 偷袭小队满员门控:2 叠加目标 + 偷袭未满 2 人 → 不偷袭(等满员)。
4. MEDIVAC 撤退复用 _lightning_step_toward(绕威胁格)。
"""

import unittest
from uuid import UUID

from arena_hero import Direction, MoveAction, ShootAction, UnitType, UnitView
from arena_hero_strategy import (
    TacticMemory,
    SmartTactic,
    MovementPlanner,
    _enemy_rangers_at_cell,
)
from test_arena_hero_tactic import (
    enemy_ranger,
    enemy_vanguard,
    enemy_worker,
    make_turn,
    core,
    ranger,
    RANGER_ID,
)


class PassiveTradeTests(unittest.TestCase):
    """被动 A:敌满血游侠进射程互覆盖 → 按血量判换血/逃。"""

    def test_passive_trade_when_outgunning_lone_enemy_ranger(self) -> None:
        # 单敌游侠进射程 + 我方 2 游侠(4血2攻 vs 敌 2血1攻)→ passive_trade 换血。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((598, 600), UUID(int=0xA001))
        r2 = ranger((602, 600), UUID(int=0xA002))
        target = enemy_ranger((600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(r1, r2), enemies=(target,),
        )
        tactic.choose_actions(turn)
        self.assertGreaterEqual(
            memory.decision_totals.get("ranger:passive_trade", 0), 1,
            memory.decision_totals,
        )

    def test_passive_flee_when_outgunned_by_stacked_pair(self) -> None:
        # 2 叠加敌游侠进射程 + 我方 1 游侠(2血1攻 vs 敌 4血2攻)→ passive_flee 逃。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        own = ranger((598, 600), RANGER_ID)
        e1 = enemy_ranger((600, 600), unit_id=UUID(int=0xB001))
        e2 = enemy_ranger((600, 600), unit_id=UUID(int=0xB002))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(own,), enemies=(e1, e2),
        )
        tactic.choose_actions(turn)
        self.assertGreaterEqual(
            memory.decision_totals.get("ranger:passive_flee", 0), 1,
            memory.decision_totals,
        )

    def test_passive_trade_when_even_firepower_vs_stacked(self) -> None:
        # 2 叠加敌游侠 + 我方 4 游侠(火力 ≥ 4)→ passive_trade 换血。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((598, 600), UUID(int=0xA001))
        r2 = ranger((602, 600), UUID(int=0xA002))
        r3 = ranger((600, 598), UUID(int=0xA003))
        r4 = ranger((600, 602), UUID(int=0xA004))
        e1 = enemy_ranger((600, 600), unit_id=UUID(int=0xB001))
        e2 = enemy_ranger((600, 600), unit_id=UUID(int=0xB002))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(r1, r2, r3, r4), enemies=(e1, e2),
        )
        tactic.choose_actions(turn)
        self.assertGreaterEqual(
            memory.decision_totals.get("ranger:passive_trade", 0), 1,
            memory.decision_totals,
        )


class AssaultSquadFillGateTests(unittest.TestCase):
    """偷袭小队满员门控:2 叠加目标需满 2 人,单游侠/工人/先锋 1 人即偷袭。"""

    def test_assault_plan_with_three_rangers_two_preaim_one_assault(self) -> None:
        # 目标为工人(不触发被动 A):3 游侠 → 2 预瞄 + 1 偷袭。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((598, 600), UUID(int=0xA001))
        r2 = ranger((602, 600), UUID(int=0xA002))
        r3 = ranger((600, 598), UUID(int=0xA003))
        target = enemy_worker((600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(r1, r2, r3), enemies=(target,),
        )
        planner = MovementPlanner(turn, memory, [])
        plan = tactic._ranger_assault_plan(turn, planner, set(), {})
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan.preaim_ids), 2)
        self.assertEqual(len(plan.assault_ids), 1)

    def test_assault_plan_with_two_rangers_all_preaim(self) -> None:
        # 目标为工人:2 游侠 → 2 预瞄 + 0 偷袭。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((598, 600), UUID(int=0xA001))
        r2 = ranger((602, 600), UUID(int=0xA002))
        target = enemy_worker((600, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(r1, r2), enemies=(target,),
        )
        planner = MovementPlanner(turn, memory, [])
        plan = tactic._ranger_assault_plan(turn, planner, set(), {})
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan.preaim_ids), 2)
        self.assertEqual(len(plan.assault_ids), 0)


class VanguardDanceTableTests(unittest.TestCase):
    """单杀舞步(第五节表,无状态查表)。"""

    def _run(self, own_pos, enemy_pos, hp, *, prev_pos=None):
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        if prev_pos is not None:
            warm, _ = make_turn(
                tick=1, own_core=core((560, 600)),
                units=(ranger(own_pos, RANGER_ID),),
                enemies=(enemy_vanguard(prev_pos, hp=hp),),
            )
            tactic.memory.observe(warm)
        turn, _ = make_turn(
            tick=2, own_core=core((560, 600)),
            units=(ranger(own_pos, RANGER_ID),),
            enemies=(enemy_vanguard(enemy_pos, hp=hp),),
        )
        tactic.choose_actions(turn)
        return turn, memory

    def test_dist1_hp4_backs_away(self) -> None:
        # dist1 + hp4 → DANCE_BACK 后退(朝先锋反方向 MoveAction)。
        turn, memory = self._run((600, 600), (601, 600), hp=4)
        action = turn.plan.unit_actions.get(RANGER_ID)
        self.assertIsInstance(action, MoveAction, memory.decision_totals)
        assert isinstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.LEFT)
        self.assertGreaterEqual(memory.decision_totals["ranger:vanguard_dance"], 1)

    def test_dist1_hp2_preaims_retreat_cell(self) -> None:
        # dist1 + hp2 → DANCE_PREAIM 撤退格(ShootAction)。
        turn, memory = self._run((600, 600), (601, 600), hp=2, prev_pos=(602, 600))
        action = turn.plan.unit_actions.get(RANGER_ID)
        self.assertIsInstance(action, ShootAction, memory.decision_totals)
        self.assertGreaterEqual(memory.decision_totals["ranger:vanguard_dance"], 1)

    def test_dist2_hp4_approaching_preaims_middle_cell(self) -> None:
        # dist2 + hp4 靠近 → DANCE_PREAIM 中间格。
        turn, memory = self._run((600, 600), (602, 600), hp=4, prev_pos=(603, 600))
        action = turn.plan.unit_actions.get(RANGER_ID)
        self.assertIsInstance(action, ShootAction, memory.decision_totals)
        assert isinstance(action, ShootAction)
        self.assertEqual(action.expected_cell, (601, 600))
        self.assertGreaterEqual(memory.decision_totals["ranger:vanguard_dance"], 1)

    def test_dist2_hp2_retreating_preaims_retreat_cell(self) -> None:
        # dist2 + hp2 远离(先锋上一步靠近我,现远离=prev 距我更近)→ 预瞄撤退格。
        # prev=(601,600) dist1 → current=(602,600) dist2 = 远离。
        turn, memory = self._run((600, 600), (602, 600), hp=2, prev_pos=(601, 600))
        action = turn.plan.unit_actions.get(RANGER_ID)
        self.assertIsInstance(action, ShootAction, memory.decision_totals)
        self.assertGreaterEqual(memory.decision_totals["ranger:vanguard_dance"], 1)

    def test_dist3_does_not_trigger_dance(self) -> None:
        # dist3 不触发单杀舞步(走编组/预瞄小队,无 vanguard_dance 计数)。
        turn, memory = self._run((600, 600), (603, 600), hp=4, prev_pos=(604, 600))
        # dist3 先锋不进舞步;走编组(工人? 无,这里是先锋 dist3 → assault 目标含先锋)。
        # 关键:无 vanguard_dance 计数(舞步不接管 dist3)。
        self.assertEqual(
            memory.decision_totals.get("ranger:vanguard_dance", 0), 0,
            memory.decision_totals,
        )


class PassivePriorityTests(unittest.TestCase):
    """被动优先级:逃跑 > 换血 > 舞步动作 > 预瞄。"""

    def test_vanguard_adjacent_flee_overrides_ranger_trade(self) -> None:
        # 先锋贴脸(hp4 → DANCE_BACK 退) + 敌游侠进射程(换血)同 tick → 逃/退优先。
        # 我方 1 游侠在 (600,600):先锋(601,600) hp4 贴脸 → DANCE_BACK(退);
        # 同时敌游侠(598,600)进射程互覆盖 → 但我方 1 游侠 vs 1 敌游侠火力对等(换血)。
        # 优先级:先锋贴脸触发的是 DANCE_BACK(撤退),敌游侠进射程触发换血——
        # 规格第二节:先锋贴脸逃跑 + 游侠进射程换血 → 逃(先锋贴脸优先)。
        # 但我方单游侠对等单敌游侠应换血,而先锋贴脸 hp4 是后退(撤退类)→ 取逃/退。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        own = ranger((600, 600), RANGER_ID)
        vanguard_enemy = enemy_vanguard((601, 600), hp=4)
        ranger_enemy = enemy_ranger((598, 600))
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(own,), enemies=(vanguard_enemy, ranger_enemy),
        )
        tactic.choose_actions(turn)
        # 先锋贴脸优先:游侠朝先锋反方向退(LEFT),而非射敌游侠换血。
        action = turn.plan.unit_actions.get(own.id)
        self.assertIsInstance(action, MoveAction, memory.decision_totals)
        assert isinstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.LEFT)

    def test_ranger_in_range_trade_overrides_vanguard_dist2_preaim(self) -> None:
        # 先锋 dist2 预瞄 + 敌游侠进射程换血同 tick → 换血(互覆盖致预瞄作废)。
        # 我方游侠(600,600):先锋(602,600) dist2 hp4 靠近(预瞄中间格)+ 敌游侠(598,600)
        # 进射程互覆盖(换血)。优先级:进射程换血 > dist2 预瞄。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # warmup 让先锋 prev=(603,600)(朝我靠近)。
        warm, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(ranger((600, 600), RANGER_ID),),
            enemies=(enemy_vanguard((603, 600), hp=4),),
        )
        tactic.memory.observe(warm)
        own = ranger((600, 600), RANGER_ID)
        vanguard_enemy = enemy_vanguard((602, 600), hp=4)
        ranger_enemy = enemy_ranger((598, 600))
        turn, _ = make_turn(
            tick=2, own_core=core((560, 600)),
            units=(own,), enemies=(vanguard_enemy, ranger_enemy),
        )
        tactic.choose_actions(turn)
        # 进射程换血优先:射敌游侠原位(598,600),而非预瞄先锋中间格。
        self.assertGreaterEqual(
            memory.decision_totals.get("ranger:passive_trade", 0), 1,
            memory.decision_totals,
        )


class EnemyRangersAtCellHelperTests(unittest.TestCase):
    def test_counts_only_same_cell_rangers(self) -> None:
        r1 = ranger((598, 600), RANGER_ID)
        e1 = enemy_ranger((600, 600), unit_id=UUID(int=0xB001))
        e2 = enemy_ranger((600, 600), unit_id=UUID(int=0xB002))
        e3 = enemy_ranger((601, 600), unit_id=UUID(int=0xB003))  # 相邻,不同格
        wk = enemy_worker((600, 600))  # 同格但工人不计
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(r1,), enemies=(e1, e2, e3, wk),
        )
        self.assertEqual(_enemy_rangers_at_cell(turn, (600, 600)), 2)


class MedivacThreatMemoryTests(unittest.TestCase):
    """MEDIVAC 残血撤退复用 _lightning_step_toward,绕开威胁记忆格。"""

    def test_medivac_avoids_threat_memory_cell(self) -> None:
        from arena_hero_strategy import THREAT_MEMORY_TICKS
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        memory.recent_threat_cells[(580, 600)] = 10 + THREAT_MEMORY_TICKS
        own = ranger((598, 600), RANGER_ID, hp=1)
        turn, _ = make_turn(
            tick=10, own_core=core((560, 600)),
            units=(own,),
        )
        planner = MovementPlanner(turn, memory, [])
        moved = tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], (560, 600), "ranger_medivac"
        )
        self.assertTrue(moved)
        action = turn.plan.unit_actions.get(own.id)
        self.assertIsNotNone(action)
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.LEFT)


if __name__ == "__main__":
    unittest.main()
