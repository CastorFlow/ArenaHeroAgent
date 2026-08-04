"""端到端集成验证：模拟真实回合 → 策略决策 → memory/stats 落盘 → server 读取。"""
from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from arena_hero import (
    Accepted,
    ChampionBeacon,
    CommandSource,
    CoreState,
    CoreView,
    Direction,
    PlayerState,
    PlayerStatus,
    TerrainView,
    Turn,
    UnitType,
    UnitView,
)

from arena_hero_route_overlay_server import create_server
from arena_hero_strategy import ROUTES_FILENAME, SmartTactic, TacticMemory

CORE_ID = UUID("00000000-0000-4000-8000-000000000100")
WORKER_A = UUID("00000000-0000-4000-8000-000000000001")
RANGER_A = UUID("00000000-0000-4000-8000-000000000003")
ENEMY_CORE = UUID("00000000-0000-4000-8000-000000000200")


def _turn(tick: int, *, resources: int, units: tuple[UnitView | CoreView, ...]) -> Turn:
    objects: list = list(units)
    state = PlayerState(
        status=PlayerStatus.ACTIVE,
        respawn_at_tick=None,
        resources=resources,
        population=sum(1 for u in units if isinstance(u, UnitView)),
        population_tier=0,
        upkeep_next_tick=0,
        champion_beacon=ChampionBeacon(position=(40, 40)),
        objects=tuple(objects),
        events=(),
    )

    def submitter(plan, idempotency_key):
        return Accepted(
            accepted=True,
            tick=plan.tick,
            source=CommandSource.AGENT,
            received_at=datetime.now(timezone.utc),
        )

    return Turn(tick=tick, state=state, submitter=submitter)


def _core(position=(5, 5)) -> CoreView:
    return CoreView(
        kind="CORE",
        id=CORE_ID,
        controlled=True,
        owner_username="test_hero",
        position=position,
        hp=5,
        shield=5,
        state=CoreState.NORMAL,
    )


def _worker(position=(6, 5)) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=WORKER_A,
        controlled=True,
        position=position,
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=0,
    )


class EndToEndTests(unittest.TestCase):
    def test_full_pipeline(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            memory_path = root / ".arena_hero_memory.json"
            routes_path = root / ROUTES_FILENAME
            stats_path = root / ".arena_hero_stats.json"
            control_path = root / ".arena_hero_control.json"
            control_path.write_text(
                json.dumps({"mode": "aggress", "recall": False}),
                encoding="utf-8",
            )

            # 1) 策略决策（aggress 模式）
            turn = _turn(
                12,
                resources=25,
                units=(_core(), _worker(), ranger := UnitView(
                    kind="UNIT",
                    id=RANGER_A,
                    controlled=True,
                    position=(9, 5),
                    hp=2,
                    unit_type=UnitType.RANGER,
                )),
            )
            memory = TacticMemory()
            tactic = SmartTactic(memory, control_path=control_path)
            summary = tactic.choose_actions(turn)
            self.assertEqual(memory.mode, "aggress")
            self.assertGreaterEqual(summary.unit_actions, 0)

            # 2) 模拟 submit 成功后 memory.save + write_stats
            memory.save(memory_path)
            memory.write_stats(stats_path, turn)
            self.assertTrue(memory_path.is_file())
            self.assertTrue(routes_path.is_file())
            self.assertTrue(stats_path.is_file())

            # 3) server 读取 routes + stats + control
            server = create_server(routes_path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                import urllib.request

                with urllib.request.urlopen(f"http://{host}:{port}/routes", timeout=2) as response:
                    routes_payload = json.load(response)
                with urllib.request.urlopen(f"http://{host}:{port}/stats", timeout=2) as response:
                    stats_payload = json.load(response)
                with urllib.request.urlopen(f"http://{host}:{port}/control", timeout=2) as response:
                    control_payload = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertEqual(routes_payload["tick"], 12)
            self.assertEqual(stats_payload["mode"], "aggress")
            self.assertEqual(stats_payload["resources"], 25)
            self.assertEqual(
            control_payload,
            {
                "mode": "aggress",
                "recall": False,
                "beacon_target_distance": 0,
                "rally_point": None,
                "aggress_vanguards": 0,
                "aggress_rangers": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
