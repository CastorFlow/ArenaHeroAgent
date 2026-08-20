"""战况历史 + Core 轨迹 JSONL 数据层测试。

覆盖 arena_hero_strategy.py 的 _append_battle_history / _append_core_trail：
- 敌方单位击杀按兵种计数（enemy_sightings 提供兵种）
- 我方阵亡按兵种计数（lightning_recent_deaths 为准）
- 射击命中/落空计数
- 敌核击杀去重（battle_enemy_cores_seen）
- Core 轨迹逐 tick 落行
- env 未设置时零副作用（不落盘）
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from arena_hero import ResolutionEvent

import arena_hero_strategy as strategy_module
from test_arena_hero_tactic import (
    ENEMY_CORE_ID,
    ENEMY_RANGER_ID,
    WORKER_LOW,
    core,
    enemy_ranger,
    make_turn,
    worker,
)

SmartTactic = strategy_module.SmartTactic
TacticMemory = strategy_module.TacticMemory
EnemySighting = strategy_module.EnemySighting


def _event(
    event_type: str,
    *,
    actor_id: UUID | None = None,
    target_id: UUID | None = None,
    hp: int = 0,
    reason_code: str | None = None,
) -> ResolutionEvent:
    return ResolutionEvent(
        event_id=UUID(int=0xA000),
        tick=50,
        event_type=event_type,
        reason_code=reason_code,
        actor_id=actor_id,
        target_id=target_id,
        position=(600, 600),
        values={"hp": hp} if event_type == "UNIT_DAMAGED" else None,
    )


class BattleHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.battle_path = Path(self._tmp.name) / "battle.jsonl"
        self.old_env = os.environ.get("ARENA_HERO_BATTLE_HISTORY_FILE")
        os.environ["ARENA_HERO_BATTLE_HISTORY_FILE"] = str(self.battle_path)

    def tearDown(self) -> None:
        if self.old_env is None:
            os.environ.pop("ARENA_HERO_BATTLE_HISTORY_FILE", None)
        else:
            os.environ["ARENA_HERO_BATTLE_HISTORY_FILE"] = self.old_env

    def _read(self) -> list[dict]:
        if not self.battle_path.is_file():
            return []
        return [
            json.loads(line)
            for line in self.battle_path.read_text(encoding="utf-8").splitlines()
        ]

    def _history(self, turn, *, memory: TacticMemory | None = None) -> list[dict]:
        memory = memory or TacticMemory()
        tactic = SmartTactic(memory)
        tactic._append_battle_history(turn)
        return self._read()

    def test_enemy_unit_kills_counted_by_type(self) -> None:
        memory = TacticMemory()
        memory.enemy_sightings[str(ENEMY_RANGER_ID)] = EnemySighting(
            position=(1, 1), seen_tick=1, is_core=False, unit_type="RANGER"
        )
        turn, _ = make_turn(
            tick=50,
            own_core=core((600, 600)),
            units=(worker(WORKER_LOW, (600, 590)),),
            events=(
                _event("UNIT_DAMAGED", actor_id=WORKER_LOW, target_id=ENEMY_RANGER_ID, hp=0),
                _event("SHOT_HIT"),
                _event("SHOT_MISSED"),
            ),
        )
        records = self._history(turn, memory=memory)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["kills"]["enemy_ranger"], 1)
        self.assertEqual(record["shots"], {"hit": 1, "miss": 1})
        self.assertEqual(record["core_destroyed"], False)

    def test_unknown_enemy_type_falls_back(self) -> None:
        turn, _ = make_turn(
            tick=50,
            own_core=core((600, 600)),
            units=(worker(WORKER_LOW, (600, 590)),),
            events=(_event("UNIT_DAMAGED", target_id=ENEMY_RANGER_ID, hp=0),),
        )
        record = self._history(turn)[0]
        # enemy_sightings 里没有该 target → enemy_unknown
        self.assertEqual(record["kills"]["enemy_unknown"], 1)

    def test_our_losses_from_recent_deaths(self) -> None:
        memory = TacticMemory()
        memory.lightning_recent_deaths = {str(WORKER_LOW): "WORKER"}
        turn, _ = make_turn(tick=50, own_core=core((600, 600)))
        record = self._history(turn, memory=memory)[0]
        self.assertEqual(record["losses"]["worker"], 1)
        self.assertEqual(record["losses"]["ranger"], 0)

    def test_friendly_death_not_double_counted_as_enemy_kill(self) -> None:
        # 同一我方单位同时出现在 UNIT_DAMAGED(hp=0) 与 lightning_recent_deaths
        # 时，只计一次阵亡、不计击杀（防删除前后不同步导致的双计/错计）。
        memory = TacticMemory()
        memory.lightning_recent_deaths = {str(WORKER_LOW): "WORKER"}
        turn, _ = make_turn(
            tick=50,
            own_core=core((600, 600)),
            events=(
                _event("UNIT_DAMAGED", target_id=WORKER_LOW, hp=0),
            ),
        )
        record = self._history(turn, memory=memory)[0]
        self.assertEqual(record["losses"]["worker"], 1)
        self.assertEqual(record["kills"]["enemy_unknown"], 0)
        self.assertEqual(sum(record["kills"].values()), 0)

    def test_enemy_core_kill_deduped(self) -> None:
        memory = TacticMemory()
        events = (
            _event(
                "DESTRUCTION_PARTICIPATION",
                target_id=ENEMY_CORE_ID,
                reason_code="CORE",
            ),
        )
        turn, _ = make_turn(tick=50, own_core=core((600, 600)), events=events)
        first = self._history(turn, memory=memory)[0]
        self.assertEqual(first["kills"]["enemy_core"], 1)
        self.assertIn(str(ENEMY_CORE_ID), memory.battle_enemy_cores_seen)
        # 同一敌核再来一次 → 去重不重复计
        turn2, _ = make_turn(tick=51, own_core=core((600, 600)), events=events)
        self._history(turn2, memory=memory)
        records = self._read()
        self.assertEqual(sum(r["kills"]["enemy_core"] for r in records), 1)

    def test_quiet_tick_writes_nothing(self) -> None:
        turn, _ = make_turn(tick=50, own_core=core((600, 600)))
        self._history(turn)
        self.assertEqual(self._read(), [])

    def test_no_env_path_is_noop(self) -> None:
        os.environ.pop("ARENA_HERO_BATTLE_HISTORY_FILE", None)
        tactic = SmartTactic()  # 不设 env → battle_history_path None
        self.assertIsNone(tactic.battle_history_path)
        turn, _ = make_turn(tick=50, own_core=core((600, 600)))
        tactic.choose_actions(turn)
        self.assertFalse(Path("arena_hero_battle_history.jsonl").exists())


class CoreTrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trail_path = Path(self._tmp.name) / "trail.jsonl"
        self.old_env = os.environ.get("ARENA_HERO_CORE_TRAIL_FILE")
        os.environ["ARENA_HERO_CORE_TRAIL_FILE"] = str(self.trail_path)

    def tearDown(self) -> None:
        if self.old_env is None:
            os.environ.pop("ARENA_HERO_CORE_TRAIL_FILE", None)
        else:
            os.environ["ARENA_HERO_CORE_TRAIL_FILE"] = self.old_env

    def test_trail_written_per_tick(self) -> None:
        memory = TacticMemory()
        memory.core_target = (100, 100)
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            tick=50,
            own_core=core((600, 600)),
            units=(worker(WORKER_LOW, (600, 590)),),
        )
        tactic.choose_actions(turn)
        lines = self.trail_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["tick"], 50)
        self.assertEqual(record["pos"], [600, 600])
        self.assertEqual(record["hp"], 5)
        self.assertEqual(record["target"], [100, 100])
        self.assertEqual(record["transfer_mode"], "star")

    def test_trail_noop_without_env(self) -> None:
        os.environ.pop("ARENA_HERO_CORE_TRAIL_FILE", None)
        tactic = SmartTactic()
        self.assertIsNone(tactic.core_trail_path)
        turn, _ = make_turn(tick=50, own_core=core((600, 600)))
        tactic.choose_actions(turn)
        self.assertFalse(Path("arena_hero_core_trail.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
