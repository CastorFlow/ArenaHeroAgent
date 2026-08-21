"""退避三舍 / 趁胜追击 / 御驾亲征 单元测试。

覆盖 arena_hero_strategy.py 的三处新增：
- core_evade_enemies：敌方入视野时 Core 迁移方向远离敌方（_core_enemy_bias → "evade"）
- core_chase_enemies：敌方入视野时 Core 迁移方向靠近敌方（_core_enemy_bias → "chase"）
- core_pursue_beacon：core_target 每 tick 动态指向信标位置，core_target_kind="beacon"，
  到达不停驻、持续跟随；关闭则清 target 回落恒星巡逻。
- 退避与追击互斥（同时开退避优先）；敌方视野消失 → "none"；优先级低于驻扎。
"""
from __future__ import annotations

import unittest
from pathlib import Path

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
from arena_hero import ChampionBeacon

SmartTactic = strategy_module.SmartTactic
TacticMemory = strategy_module.TacticMemory


def _decisions(memory, own_core, units, *, enemies=(), beacon=None, tick=8):
    turn, _ = make_turn(
        tick=tick,
        own_core=own_core,
        units=units,
        enemies=enemies,
        beacon=beacon,
    )
    tactic = SmartTactic(memory)
    return tactic.choose_actions(turn).decisions


class CoreEnemyBiasTests(unittest.TestCase):
    def _memory(self) -> TacticMemory:
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        return memory

    def test_bias_none_when_no_enemy_visible(self) -> None:
        memory = self._memory()
        memory.core_evade_enemies = True
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((610, 600), RANGER_ID), worker(WORKER_LOW, (600, 590))),
        )
        tactic = SmartTactic(memory)
        # 无可见敌方 → "none"，Core 沿恒星轨道巡逻。
        self.assertEqual(tactic._core_enemy_bias(turn), "none")

    def test_bias_evade_when_enemy_visible(self) -> None:
        memory = self._memory()
        memory.core_evade_enemies = True
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((610, 600), RANGER_ID), worker(WORKER_LOW, (600, 590))),
            enemies=(enemy_ranger((620, 600)),),
        )
        tactic = SmartTactic(memory)
        self.assertEqual(tactic._core_enemy_bias(turn), "evade")

    def test_bias_chase_when_enemy_visible(self) -> None:
        memory = self._memory()
        memory.core_chase_enemies = True
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((610, 600), RANGER_ID), worker(WORKER_LOW, (600, 590))),
            enemies=(enemy_ranger((620, 600)),),
        )
        tactic = SmartTactic(memory)
        self.assertEqual(tactic._core_enemy_bias(turn), "chase")

    def test_evade_takes_priority_over_chase(self) -> None:
        # 退避与追击同时开 → 退避优先（保命优先于抢攻）。
        memory = self._memory()
        memory.core_evade_enemies = True
        memory.core_chase_enemies = True
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((610, 600), RANGER_ID), worker(WORKER_LOW, (600, 590))),
            enemies=(enemy_ranger((620, 600)),),
        )
        tactic = SmartTactic(memory)
        self.assertEqual(tactic._core_enemy_bias(turn), "evade")

    def test_evade_lower_priority_than_hold(self) -> None:
        # 驻扎最高优先级：开启时 Core 不动，不进迁移（不调用 _core_enemy_bias）。
        memory = self._memory()
        memory.core_hold = True
        memory.core_evade_enemies = True
        decisions = _decisions(
            memory,
            core((600, 600)),
            (ranger((610, 600), RANGER_ID), worker(WORKER_LOW, (600, 590))),
            enemies=(enemy_ranger((620, 600)),),
        )
        text = " | ".join(decisions)
        self.assertIn("core hold=true", text)
        self.assertNotIn("lightning patrol", text)
        self.assertNotIn("lightning core_transfer", text)

    def test_evade_still_patrols_when_enemy_disappears(self) -> None:
        # 敌方视野消失 → 偏置 "none"，Core 恢复恒星轨道巡逻（不卡死在退避态）。
        memory = self._memory()
        memory.core_evade_enemies = True
        decisions = _decisions(
            memory,
            core((600, 600)),
            (ranger((610, 600), RANGER_ID), worker(WORKER_LOW, (600, 590))),
            enemies=(),  # 无敌方
        )
        text = " | ".join(decisions)
        self.assertIn("lightning patrol", text)


class CorePursueBeaconTests(unittest.TestCase):
    def test_pursue_sets_target_to_beacon_position(self) -> None:
        # 御驾亲征：core_target 每 tick 指向信标位置，core_target_kind="beacon"。
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_pursue_beacon = True
        beacon = ChampionBeacon(position=(100, 100))
        _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 590)),),
            beacon=beacon,
        )
        self.assertEqual(memory.core_target, (100, 100))
        self.assertEqual(memory.core_target_kind, "beacon")

    def test_pursue_no_auto_hold_on_arrival(self) -> None:
        # 到达信标附近不停驻（信标动态移动），持续走转移推进。
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_pursue_beacon = True
        beacon = ChampionBeacon(position=(600, 604))  # 距 Core 4 格 < 到达死区 8
        decisions = _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 590)),),
            beacon=beacon,
        )
        text = " | ".join(decisions)
        self.assertIn("kind=beacon", text)
        self.assertNotIn("core target_arrived", text)
        self.assertFalse(memory.core_hold)
        # core_target 仍指向信标（御驾亲征保持开启，不因到达清 target）。
        self.assertEqual(memory.core_target, (600, 604))
        self.assertEqual(memory.core_target_kind, "beacon")

    def test_pursue_lower_priority_than_hold(self) -> None:
        # 驻扎最高优先级：开启时御驾亲征不生效（Core 不动）。
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_pursue_beacon = True
        memory.core_hold = True
        beacon = ChampionBeacon(position=(100, 100))
        decisions = _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 590)),),
            beacon=beacon,
        )
        text = " | ".join(decisions)
        self.assertIn("core hold=true", text)
        self.assertNotIn("kind=beacon", text)

    def test_pursue_overrides_persisted_core_target(self) -> None:
        # 御驾亲征开启时无视持久化的 core_target（手设目标让位信标跟随）。
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        memory.core_pursue_beacon = True
        memory.core_target = (200, 200)  # 遗留的旧目标
        memory.core_target_kind = "user"
        beacon = ChampionBeacon(position=(300, 300))
        _decisions(
            memory,
            core((600, 600)),
            (worker(WORKER_LOW, (600, 590)),),
            beacon=beacon,
        )
        self.assertEqual(memory.core_target, (300, 300))
        self.assertEqual(memory.core_target_kind, "beacon")


class CoreEnemyBiasPersistTests(unittest.TestCase):
    def _tmp_control(self, tmpdir, payload) -> "Path":
        import json
        path = Path(tmpdir) / ".arena_hero_control.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_apply_control_loads_new_flags(self) -> None:
        import tempfile
        memory = TacticMemory()
        memory.core_evade_enemies = False
        memory.core_chase_enemies = False
        memory.core_pursue_beacon = False
        with tempfile.TemporaryDirectory() as tmp:
            path = self._tmp_control(
                tmp,
                {
                    "core_evade_enemies": True,
                    "core_chase_enemies": True,
                    "core_pursue_beacon": True,
                },
            )
            memory.load_control(path)
        self.assertTrue(memory.core_evade_enemies)
        self.assertTrue(memory.core_chase_enemies)
        self.assertTrue(memory.core_pursue_beacon)

    def test_disabling_pursue_clears_beacon_target(self) -> None:
        # 关闭御驾亲征时清掉 beacon 目标，回落恒星巡逻/用户手设目标。
        import tempfile
        memory = TacticMemory()
        memory.core_pursue_beacon = True
        memory.core_target = (100, 100)
        memory.core_target_kind = "beacon"
        with tempfile.TemporaryDirectory() as tmp:
            path = self._tmp_control(tmp, {"core_pursue_beacon": False})
            memory.load_control(path)
        self.assertFalse(memory.core_pursue_beacon)
        self.assertIsNone(memory.core_target)
        self.assertEqual(memory.core_target_kind, "user")

    def test_save_load_round_trips_new_flags(self) -> None:
        import tempfile
        memory = TacticMemory()
        memory.core_evade_enemies = True
        memory.core_chase_enemies = True
        memory.core_pursue_beacon = True
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory.save(path)
            restored = TacticMemory.load(path)
        self.assertTrue(restored.core_evade_enemies)
        self.assertTrue(restored.core_chase_enemies)
        self.assertTrue(restored.core_pursue_beacon)


if __name__ == "__main__":
    unittest.main()
