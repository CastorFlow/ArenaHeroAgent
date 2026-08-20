"""网页控制台新增控制：Core 驻扎/目标坐标/三种转移模式的单元测试。

覆盖（对应 arena_hero_strategy.py 的 _choose_core / _choose_workers 新增分支）：
- core_hold：Core 停驻，行星照常巡逻
- core_target：Core 向目标迁移（lightning core_transfer）
- core_orbit_radius：覆盖巡逻半径
- march：工人停止采集回归轨道（worker:march）
- fortify：有货工人不提交、回轨道带货等待（worker:fortify_hold），到达后恢复交付
- star：默认行为不变（worker:deposit）
"""
from __future__ import annotations

import unittest
from uuid import UUID

import arena_hero_strategy as strategy_module
from test_arena_hero_tactic import (
    RANGER_ID,
    WORKER_LOW,
    core,
    enemy_ranger,
    make_turn,
    ranger,
    worker,
)

SmartTactic = strategy_module.SmartTactic
TacticMemory = strategy_module.TacticMemory


def _decisions(memory: TacticMemory, own_core, units, *, tick: int = 8) -> tuple[str, ...]:
    turn, _ = make_turn(tick=tick, own_core=own_core, units=units)
    tactic = SmartTactic(memory)
    return tactic.choose_actions(turn).decisions


class CoreHoldTests(unittest.TestCase):
    def test_core_hold_stops_patrol(self) -> None:
        memory = TacticMemory()
        memory.core_hold = True
        memory.core_orbit_radius = 550
        decisions = _decisions(
            memory,
            core((600, 600)),
            (ranger((610, 600), RANGER_ID), worker(WORKER_LOW, (600, 590))),
        )
        text = " | ".join(decisions)
        self.assertIn("core hold=true", text)
        self.assertNotIn("lightning patrol", text)

    def test_core_hold_keeps_units_acting(self) -> None:
        memory = TacticMemory()
        memory.core_hold = True
        memory.core_orbit_radius = 550
        decisions = _decisions(
            memory,
            core((600, 600)),
            (ranger((610, 600), RANGER_ID), worker(WORKER_LOW, (600, 590))),
        )
        text = " | ".join(decisions)
        # 驻扎期间行星照常巡逻（游侠/工人仍有动作）
        self.assertIn("ranger:", text)
        self.assertIn("worker:", text)


class CoreTargetTests(unittest.TestCase):
    def test_core_target_triggers_transfer(self) -> None:
        memory = TacticMemory()
        memory.core_target = (100, 100)
        memory.core_orbit_radius = 550
        decisions = _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 590)),),
        )
        text = " | ".join(decisions)
        self.assertIn("lightning core_transfer target=(100, 100)", text)

    def test_core_target_arrived_parks(self) -> None:
        memory = TacticMemory()
        memory.core_target = (600, 604)  # 距 Core 4 格 < 到达死区 8
        memory.core_orbit_radius = 550
        decisions = _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 590)),),
        )
        text = " | ".join(decisions)
        self.assertIn("core target_arrived", text)
        self.assertNotIn("lightning core_transfer", text)

    def test_user_target_arrived_auto_holds_and_clears_target(self) -> None:
        # 到达用户目标 → auto 开驻扎 + 清 target（急行军/坚壁清野限制不再拖累工人）。
        memory = TacticMemory()
        memory.core_target = (600, 604)
        memory.core_target_kind = "user"
        memory.core_orbit_radius = 550
        _decisions(memory, core((600, 600)), (worker(WORKER_LOW, (600, 590)),))
        self.assertTrue(memory.core_hold)
        self.assertIsNone(memory.core_target)

    def test_orbit_arrived_starts_patrol_not_hold(self) -> None:
        # r 变更触发的轨道迁移：到达轨道角 → 清 target + 不驻扎，直接开始巡逻。
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_target = (550, 550)  # 轨道角，Core 就在角上
        memory.core_target_kind = "orbit"
        decisions = _decisions(memory, core((550, 550)), (worker(WORKER_LOW, (556, 550)),))
        text = " | ".join(decisions)
        self.assertIn("orbit_arrived", text)
        self.assertFalse(memory.core_hold)
        self.assertIsNone(memory.core_target)

    def test_obstacle_target_reached_by_adjacency(self) -> None:
        # 目标格本身是障碍物 → Core 贴到目标 1 格内即视为到达（不要求踩上去）。
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_target = (610, 600)  # 障碍物
        memory.core_target_kind = "user"
        turn, _ = make_turn(
            tick=8,
            own_core=core((608, 600)),  # 距目标 2 格 ≤ 死区+1
            units=(worker(WORKER_LOW, (600, 590)),),
        )
        # 把目标格标记为障碍（known_obstacles）。
        memory.known_obstacles.add((610, 600))
        decisions = SmartTactic(memory).choose_actions(turn).decisions
        text = " | ".join(decisions)
        self.assertIn("core target_arrived", text)


    def test_core_orbit_radius_drives_patrol_radius(self) -> None:
        tactic = SmartTactic()
        tactic.memory.core_orbit_radius = 520
        self.assertEqual(tactic._lightning_patrol_radius(), 520)
        tactic.memory.core_orbit_radius = 0
        # r=0 未设置，巡逻半径为 0（不巡逻）。
        self.assertEqual(tactic._lightning_patrol_radius(), 0)


class TransferModeTests(unittest.TestCase):
    def _memory(self, mode: str, target: tuple[int, int]) -> TacticMemory:
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_target = target
        memory.core_transfer_mode = mode
        return memory

    def test_march_redirects_empty_worker_to_orbit(self) -> None:
        memory = self._memory("march", (100, 100))
        decisions = _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 590)),),
        )
        text = " | ".join(decisions)
        self.assertIn("worker:", text)
        self.assertIn("march", text)
        self.assertNotIn("visible_resource", text)

    def test_fortify_holds_cargo_worker(self) -> None:
        memory = self._memory("fortify", (100, 100))
        decisions = _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 600), cargo=3),),
        )
        text = " | ".join(decisions)
        # 坚壁清野：有货工人不提交，回轨道带货等待（reason=worker_fortify_hold）
        self.assertIn("worker_fortify_hold", text)
        self.assertNotIn("deposit", text)

    def test_fortify_arrived_resumes_deposit(self) -> None:
        memory = self._memory("fortify", (600, 604))
        decisions = _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 600), cargo=3),),
        )
        text = " | ".join(decisions)
        self.assertNotIn("fortify_hold", text)
        self.assertIn("deposit", text)

    def test_star_deposits_normally(self) -> None:
        memory = self._memory("star", (100, 100))
        decisions = _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 600), cargo=3),),
        )
        text = " | ".join(decisions)
        self.assertIn("deposit", text)
        self.assertNotIn("fortify_hold", text)


class UserCommandOverridesAnchorTests(unittest.TestCase):
    """回归：网页控制台 core_hold / core_target 必须压过自主 anchor_hold。

    实战中只要存在任何 T3/T4 威胁或 funnel 空缺，_lightning_anchor_state 就会返回
    COMBAT_ANCHOR，旧实现让 anchor_hold 早返回吞掉了玩家下达的驻扎/转移指令——
    Core 永远不挪窝。这一组用近距离敌游侠强制 COMBAT_ANCHOR 复现并锁死修复。
    """

    def _decisions(self, memory, enemies=()) -> tuple[str, ...]:
        turn, _ = make_turn(
            tick=8,
            own_core=core((600, 600)),
            units=(
                worker(WORKER_LOW, (600, 590)),
                ranger((610, 600), RANGER_ID),
            ),
            enemies=enemies,
        )
        return SmartTactic(memory).choose_actions(turn).decisions

    def test_core_target_survives_combat_anchor(self) -> None:
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_target = (100, 100)
        memory.core_transfer_mode = "march"
        text = " | ".join(self._decisions(memory, enemies=(enemy_ranger((604, 600)),)))
        self.assertIn("lightning core_transfer target=(100, 100)", text)
        self.assertNotIn("core anchor_hold", text)

    def test_core_hold_survives_combat_anchor(self) -> None:
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_hold = True
        text = " | ".join(self._decisions(memory, enemies=(enemy_ranger((604, 600)),)))
        self.assertIn("core hold=true", text)
        self.assertNotIn("core anchor_hold", text)

    def test_fortify_nearby_cargo_does_not_block_core(self) -> None:
        # 坚壁清野：近圈带货工人在 Core 身边，Core 仍须向目标推进，不被
        # logistics_hold 永久卡死。
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_target = (100, 100)
        memory.core_transfer_mode = "fortify"
        turn, _ = make_turn(
            tick=8,
            own_core=core((600, 600)),
            units=(worker(WORKER_LOW, (600, 595), cargo=3),),
        )
        text = " | ".join(SmartTactic(memory).choose_actions(turn).decisions)
        self.assertIn("lightning core_transfer target=(100, 100)", text)
        self.assertIn("core start_move", text)
        self.assertNotIn("logistics_hold", text)


if __name__ == "__main__":
    unittest.main()
