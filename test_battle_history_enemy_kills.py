"""敌方单位击杀按兵种入账 + 去重的回归测试。

背景：服务器在敌方单位阵亡时只发 DESTRUCTION_PARTICIPATION(reason=UNIT)，
不发 fatal UNIT_DAMAGED。旧版 _append_battle_history 只处理 reason=CORE，
导致敌方游侠/先锋/工人击杀全部漏记（图上只剩"敌核"柱）。此测试覆盖：
- DESTRUCTION_PARTICIPATION(UNIT) → 按兵种分桶
- 兵种来自 enemy_type_snapshot（observe 前冻结，不受 sighting 清理影响）
- 同一敌方单位 UNIT_DAMAGED(hp=0) 与 DESTRUCTION_PARTICIPATION(UNIT) 双发不双计
- 跨 tick 同 target 不重复累计（battle_enemy_units_seen 持久化）
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from arena_hero import ResolutionEvent
from arena_hero_strategy import SmartTactic, TacticMemory
from test_arena_hero_tactic import (
    ENEMY_RANGER_ID,
    core,
    enemy_ranger,
    enemy_vanguard,
    enemy_worker,
    make_turn,
)

ENEMY_VANGUARD_ID = UUID(int=0x8002)
ENEMY_WORKER_ID = UUID(int=0x8001)


def _destruction_participation(target_id: UUID, reason: str) -> ResolutionEvent:
    return ResolutionEvent(
        event_id=UUID(int=int.from_bytes(target_id.bytes[:7], "big") ^ 0xF0),
        tick=9,
        event_type="DESTRUCTION_PARTICIPATION",
        reason_code=reason,
        actor_id=None,
        target_id=target_id,
        position=(610, 600),
        values=None,
    )


def _unit_damaged_fatal(target_id: UUID) -> ResolutionEvent:
    return ResolutionEvent(
        event_id=UUID(int=int.from_bytes(target_id.bytes[:6], "big") ^ 0xAA),
        tick=9,
        event_type="UNIT_DAMAGED",
        reason_code="ATTACK",
        actor_id=None,
        target_id=target_id,
        position=(610, 600),
        values={"damage": 1, "hp": 0},
    )


class BattleHistoryEnemyKillTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._battle_path = Path(self._tmp.name) / "battle.jsonl"
        self._prev_battle = os.environ.get("ARENA_HERO_BATTLE_HISTORY_FILE")
        os.environ["ARENA_HERO_BATTLE_HISTORY_FILE"] = str(self._battle_path)

    def tearDown(self) -> None:
        if self._prev_battle is None:
            os.environ.pop("ARENA_HERO_BATTLE_HISTORY_FILE", None)
        else:
            os.environ["ARENA_HERO_BATTLE_HISTORY_FILE"] = self._prev_battle
        self._tmp.cleanup()

    def _last_row(self) -> dict:
        lines = self._battle_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines, "battle history was not written")
        return json.loads(lines[-1])

    def _sight_then_kill(self, sight_enemy, kill_event, tick_kill=10):
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn0, _ = make_turn(own_core=core((560, 600)), enemies=(sight_enemy,))
        tactic.choose_actions(turn0)
        turn1, _ = make_turn(
            own_core=core((560, 600)), enemies=(), events=(kill_event,), tick=tick_kill
        )
        tactic.choose_actions(turn1)
        return self._last_row()

    def test_destruction_participation_unit_recorded_by_type(self) -> None:
        row = self._sight_then_kill(
            enemy_ranger((610, 600)),
            _destruction_participation(ENEMY_RANGER_ID, "UNIT"),
        )
        self.assertEqual(row["kills"]["enemy_ranger"], 1)
        self.assertEqual(row["kills"]["enemy_core"], 0)
        self.assertEqual(row["kills"]["enemy_unknown"], 0)

    def test_enemy_vanguard_and_worker_separate_buckets(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn0, _ = make_turn(
            own_core=core((560, 600)),
            enemies=(enemy_vanguard((610, 600)), enemy_worker((611, 600))),
        )
        tactic.choose_actions(turn0)
        turn1, _ = make_turn(
            own_core=core((560, 600)),
            enemies=(),
            events=(
                _destruction_participation(ENEMY_VANGUARD_ID, "UNIT"),
                _destruction_participation(ENEMY_WORKER_ID, "UNIT"),
            ),
            tick=10,
        )
        tactic.choose_actions(turn1)
        row = self._last_row()
        self.assertEqual(row["kills"]["enemy_vanguard"], 1)
        self.assertEqual(row["kills"]["enemy_worker"], 1)
        self.assertEqual(row["kills"]["enemy_unknown"], 0)

    def test_no_double_count_when_both_fatal_unit_damaged_and_destruction(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn0, _ = make_turn(
            own_core=core((560, 600)), enemies=(enemy_ranger((610, 600)),)
        )
        tactic.choose_actions(turn0)
        turn1, _ = make_turn(
            own_core=core((560, 600)),
            enemies=(),
            events=(
                _unit_damaged_fatal(ENEMY_RANGER_ID),
                _destruction_participation(ENEMY_RANGER_ID, "UNIT"),
            ),
            tick=10,
        )
        tactic.choose_actions(turn1)
        row = self._last_row()
        self.assertEqual(row["kills"]["enemy_ranger"], 1)
        self.assertEqual(row["kills"]["enemy_unknown"], 0)

    def test_cross_tick_dedup_via_battle_enemy_units_seen(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn0, _ = make_turn(
            own_core=core((560, 600)), enemies=(enemy_ranger((610, 600)),)
        )
        tactic.choose_actions(turn0)
        for tk in (10, 11):
            turn, _ = make_turn(
                own_core=core((560, 600)),
                enemies=(),
                events=(_destruction_participation(ENEMY_RANGER_ID, "UNIT"),),
                tick=tk,
            )
            tactic.choose_actions(turn)
        lines = self._battle_path.read_text(encoding="utf-8").splitlines()
        total_ranger = 0
        for line in lines:
            r = json.loads(line)
            total_ranger += r.get("kills", {}).get("enemy_ranger", 0)
        self.assertEqual(total_ranger, 1)


if __name__ == "__main__":
    unittest.main()
