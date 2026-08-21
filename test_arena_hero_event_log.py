from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import UUID

from arena_hero import (
    ChampionBeacon,
    CoreState,
    CoreView,
    ResolutionEvent,
    UnitType,
    UnitView,
)

from arena_hero_event_log import ChineseEventLogger, format_resolution_event


ACTOR_ID = UUID("00000000-0000-0000-0000-000000000101")
TARGET_ID = UUID("00000000-0000-0000-0000-000000000202")


def entity_name(object_id: UUID | None, fallback: str) -> str:
    names = {ACTOR_ID: "敌方游侠", TARGET_ID: "先锋#4"}
    return names.get(object_id, fallback)


class ChineseEventLogTests(unittest.TestCase):
    def test_formats_unit_death_in_chinese(self) -> None:
        event = ResolutionEvent(
            event_id=UUID(int=1),
            tick=88,
            event_type="UNIT_DAMAGED",
            reason_code="ATTACK",
            actor_id=ACTOR_ID,
            target_id=TARGET_ID,
            position=(3, -2),
            values={"damage": 1, "hp": 0},
        )

        record = format_resolution_event(event, entity_name)

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["category"], "战斗")
        self.assertEqual(record["level"], "danger")
        self.assertEqual(record["title"], "单位阵亡")
        self.assertIn("先锋#4", record["message"])
        self.assertIn("[3, -2]", record["message"])

    def test_formats_spawn_and_removes_sensitive_values(self) -> None:
        event = ResolutionEvent(
            event_id=UUID(int=2),
            tick=89,
            event_type="CORE_SPAWN_SUCCEEDED",
            position=(0, 0),
            values={
                "unit_type": UnitType.RANGER,
                "cost": 10,
                "api_key": "must-not-leak",
                "nested": {"authorization": "must-not-leak", "ok": 1},
            },
        )

        record = format_resolution_event(event, entity_name)

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["category"], "生产")
        self.assertEqual(record["title"], "生产单位")
        self.assertIn("游侠", record["message"])
        serialized = json.dumps(record, ensure_ascii=False).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertNotIn("must-not-leak", serialized)

    def test_client_failure_is_written_as_chinese_jsonl(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            logger = ChineseEventLogger(path)

            logger.append_client_error(90, "PLAN_REJECTED")

            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "计划提交失败")
        self.assertEqual(records[0]["level"], "danger")
        self.assertIn("Agent 计划未被接受", records[0]["message"])

    def test_enemy_core_owner_and_private_unit_owner_are_logged(self) -> None:
        enemy_core = CoreView(
            kind="CORE",
            id=UUID("00000000-0000-4000-8000-000000000301"),
            controlled=False,
            owner_username="enemy_hero",
            position=(8, -4),
            hp=4,
            shield=2,
            state=CoreState.NORMAL,
        )
        enemy_unit = UnitView(
            kind="UNIT",
            id=UUID("00000000-0000-4000-8000-000000000302"),
            controlled=False,
            position=(7, -4),
            hp=2,
            unit_type=UnitType.RANGER,
        )
        turn = SimpleNamespace(
            tick=91,
            events=(),
            units=(),
            core=None,
            visible_enemies=(enemy_core, enemy_unit),
            beacon=ChampionBeacon(position=(0, 0)),
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            logger = ChineseEventLogger(path)

            logger.append_turn(turn, {})
            logger.append_turn(turn, {})

            destroyed_event = ResolutionEvent(
                event_id=UUID("00000000-0000-4000-8000-000000000303"),
                tick=92,
                event_type="DESTRUCTION_PARTICIPATION",
                reason_code="CORE",
                target_id=enemy_core.id,
                position=enemy_core.position,
            )
            resolved_turn = SimpleNamespace(
                tick=92,
                events=(destroyed_event,),
                units=(),
                core=None,
                visible_enemies=(),
                beacon=ChampionBeacon(position=(0, 0)),
            )
            logger = ChineseEventLogger(path)
            logger.append_turn(resolved_turn, {})

            records = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]

        core_records = [
            record for record in records
            if record["event_type"] == "ENEMY_CORE_SPOTTED"
        ]
        unit_records = [
            record for record in records
            if record["event_type"] == "ENEMY_UNIT_SPOTTED"
        ]
        self.assertEqual(len(core_records), 1)
        self.assertEqual(len(unit_records), 1)
        self.assertEqual(core_records[0]["values"]["owner_username"], "enemy_hero")
        self.assertIn("@enemy_hero", core_records[0]["message"])
        self.assertEqual(unit_records[0]["values"]["owner_username"], None)
        self.assertIn("官方未公开", unit_records[0]["message"])
        destruction = next(
            record for record in records
            if record["event_type"] == "DESTRUCTION_PARTICIPATION"
        )
        self.assertEqual(destruction["target"], "敌方 Core @enemy_hero")


if __name__ == "__main__":
    unittest.main()
