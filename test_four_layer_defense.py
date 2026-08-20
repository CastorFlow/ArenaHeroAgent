"""Regression coverage for the forced-lightning orbital defense.

The historical version of this module asserted the retired fixed
NEAR/MID/FAR meat-shield and breakthrough branches.  Lightning is now the
only live tactic, so these integration checks exercise its lane-derived
T0--T4 geometry, funneling, and Core anchoring with real SDK Turn objects.
"""

from __future__ import annotations

import unittest

from arena_hero import Direction, MoveAction, ShootAction
from arena_hero_strategy import CoreAnchorState, SmartTactic, TacticMemory
from test_arena_hero_tactic import (
    RANGER_ID,
    VANGUARD_ID,
    WORKER_LOW,
    core,
    enemy_vanguard,
    make_turn,
    ranger,
    vanguard,
    worker,
)


class TestFourLayerDefense(unittest.TestCase):
    """Compatibility-level checks for the current dynamic four-layer model."""

    def test_lane_geometry_replaces_fixed_rings(self) -> None:
        tactic = SmartTactic(TacticMemory())
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(
                vanguard((605, 600), VANGUARD_ID),
                ranger((610, 600), RANGER_ID),
                ranger((620, 600), RANGER_ID.__class__(int=0x301)),
                ranger((630, 600), RANGER_ID.__class__(int=0x302)),
            ),
        )
        tactic.choose_actions(turn)
        plan = tactic._lightning_plan
        self.assertIsNotNone(plan)
        assert plan is not None
        geometry = plan.geometry
        self.assertGreaterEqual(geometry.r_ranger_inner, geometry.r_vanguard + 1)
        self.assertGreaterEqual(geometry.r_ranger_outer, geometry.r_ranger_inner)
        self.assertGreater(geometry.r_sensor_outer, geometry.r_ranger_outer)
        self.assertGreaterEqual(geometry.r_screen, geometry.r_commit)

    def test_t3_funnel_preserves_a_ranger_covered_gate(self) -> None:
        tactic = SmartTactic(TacticMemory())
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((605, 602)), worker(WORKER_LOW, (608, 600))),
            enemies=(enemy_vanguard((608, 602)),),
        )
        tactic.choose_actions(turn)
        plan = tactic._lightning_plan
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.anchor, CoreAnchorState.COMBAT_ANCHOR)
        self.assertEqual(plan.funnel.gate_cell, (607, 602))
        self.assertEqual(plan.funnel.block_cells, ((608, 601),))
        # 工人先执行战斗接触撤离，不能为了旧漏斗门位继续靠近先锋。
        self.assertEqual(turn.plan.unit_actions[WORKER_LOW].direction, Direction.LEFT)

    def test_t4_ranger_uses_shot_before_repositioning(self) -> None:
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((606, 600)),),
            enemies=(enemy_vanguard((609, 600)),),
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        action = turn.plan.unit_actions[RANGER_ID]
        self.assertIsInstance(action, ShootAction)
        self.assertNotIsInstance(action, MoveAction)


if __name__ == "__main__":
    unittest.main()
