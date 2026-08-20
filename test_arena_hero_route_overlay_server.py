from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import HTTPError, Request, urlopen

from arena_hero_route_overlay_server import create_server


# 网页控制台新增控制字段的默认回显值（load_control/save_control 总会回填）。
DEFAULT_DASHBOARD_CONTROL_FIELDS = {
    "core_orbit_radius": 0,
    "core_hold": False,
    "core_target": None,
    "core_transfer_mode": "star",
    "core_evade_enemies": False,
    "core_chase_enemies": False,
    "core_pursue_beacon": False,
    "build_queue": [],
    "spawn_ratio": {"ranger": 1, "vanguard": 1, "worker": 3},
    "unit_caps": {"ranger": 0, "vanguard": 0, "worker": 20},
    "replenish_threshold": {"ranger": 0, "vanguard": 0, "worker": 0},
    "replenish_priority": ["ranger", "worker", "vanguard"],
    "wartime_reserve": 150,
    "comet_active": False,
    "comet_mode": "beacon",
    "comet_target": None,
    "comet_vanguards": 3,
    "comet_rangers": 3,
    "comet_min_reserve_vanguards": 3,
    "comet_min_reserve_rangers": 3,
    "comet_wounded_threshold": 0.5,
    "comet_rally_enabled": False,
    "comet_rally_distance": 0,
}


def expected_control(**overrides: object) -> dict:
    """构造 /control POST 返回的完整期望字典（旧字段 + 网页控制台新增字段默认值）。"""
    payload: dict = {
        "mode": "develop",
        "aggress_vanguards": 0,
        "aggress_rangers": 0,
    }
    payload.update(DEFAULT_DASHBOARD_CONTROL_FIELDS)
    payload.update(overrides)
    return payload


class RouteOverlayServerTests(unittest.TestCase):
    def test_health_and_sanitized_routes(self) -> None:
        with TemporaryDirectory() as directory:
            routes_path = Path(directory) / "routes.json"
            routes_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "tick": 42,
                        "api_key": "must-not-leak",
                        "routes": [
                            {
                                "object_id": "unit-1",
                                "object_type": "WORKER",
                                "number": 1,
                                "start": [0, 0],
                                "goal": [2, 0],
                                "path": [[0, 0], [0, -1], [1, -1], [2, -1], [2, 0]],
                                "reason": "visible_resource",
                                "complete": True,
                                "authorization": "must-not-leak",
                            }
                        ],
                        "units": [
                            {
                                "object_id": "unit-1",
                                "object_type": "WORKER",
                                "number": 1,
                                "position": [0, 0],
                                "api_key": "must-not-leak",
                            }
                        ],
                        "resources": [[2, 0]],
                    }
                ),
                encoding="utf-8",
            )
            server = create_server(routes_path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                with urlopen(f"http://{host}:{port}/health", timeout=2) as response:
                    self.assertEqual(json.load(response), {"status": "ok"})
                with urlopen(f"http://{host}:{port}/routes", timeout=2) as response:
                    payload = json.load(response)
                    self.assertIsNone(response.headers["Access-Control-Allow-Origin"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(payload["tick"], 42)
        self.assertEqual(payload["routes"][0]["path"][-1], [2, 0])
        self.assertEqual(payload["routes"][0]["number"], 1)
        self.assertEqual(payload["units"][0]["number"], 1)
        self.assertEqual(payload["resources"], [[2, 0]])
        serialized = json.dumps(payload).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertNotIn("must-not-leak", serialized)

    def test_stats_endpoint(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            stats_path = directory_path / ".arena_hero_stats.json"
            stats_path.write_text(
                json.dumps(
                    {
                        "tick": 7,
                        "mode": "aggress",
                        "resources": 3,
                        "capacity": 25,
                        "population": 5,
                        "workers": 3,
                        "vanguards": 1,
                        "rangers": 1,
                        "core_hp": 5,
                        "core_shield": 4,
                        "visible_enemies": 2,
                        "owns_beacon": False,
                        "total_resources_harvested": 12,
                        "total_resources_deposited": 10,
                        "event_totals": {
                            "CORE_SPAWN_SUCCEEDED": 2,
                            "API_KEY": 99,
                        },
                        "api_key": "must-not-leak",
                    }
                ),
                encoding="utf-8",
            )
            server = create_server(routes_path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                with urlopen(f"http://{host}:{port}/stats", timeout=2) as response:
                    payload = json.load(response)
                self.assertEqual(payload["tick"], 7)
                self.assertEqual(payload["mode"], "aggress")
                self.assertEqual(payload["workers"], 3)
                self.assertEqual(payload["total_resources_harvested"], 12)
                self.assertEqual(payload["event_totals"], {"CORE_SPAWN_SUCCEEDED": 2})
                self.assertNotIn("api_key", payload)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_logs_endpoint_returns_sanitized_chinese_entries(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            logs_path = directory_path / "arena_hero_events_zh.jsonl"
            logs_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "version": 1,
                                "recorded_at": "2026-08-05T10:11:12+08:00",
                                "tick": 88,
                                "event_id": "event-88",
                                "source": "server",
                                "category": "战斗",
                                "level": "danger",
                                "title": "单位阵亡",
                                "message": "先锋#4 在 [3, -2] 阵亡",
                                "event_type": "UNIT_DAMAGED",
                                "reason_code": "ATTACK",
                                "position": [3, -2],
                                "actor": "敌方游侠",
                                "target": "先锋#4",
                                "values": {"api_key": "must-not-leak"},
                                "authorization": "must-not-leak",
                            },
                            ensure_ascii=False,
                        ),
                        "not-json",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            server = create_server(routes_path, logs_path=logs_path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                with urlopen(f"http://{host}:{port}/logs", timeout=2) as response:
                    payload = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(payload["latest_tick"], 88)
        self.assertEqual(len(payload["entries"]), 1)
        self.assertEqual(payload["entries"][0]["title"], "单位阵亡")
        self.assertEqual(payload["entries"][0]["position"], [3, -2])
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertNotIn("must-not-leak", serialized)

    def test_control_get_post_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            control_path = directory_path / ".arena_hero_control.json"
            server = create_server(routes_path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            base = f"http://{host}:{port}"
            try:
                with urlopen(f"{base}/control", timeout=2) as response:
                    default = json.load(response)
                self.assertEqual(default, expected_control())

                request = Request(
                    f"{base}/control",
                    data=json.dumps({"mode": "aggress"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    posted = json.load(response)
                self.assertEqual(posted, expected_control(mode="aggress"))

                with urlopen(f"{base}/control", timeout=2) as response:
                    after = json.load(response)
                self.assertEqual(after, expected_control(mode="aggress"))
                self.assertEqual(
                    json.loads(control_path.read_text(encoding="utf-8")),
                    expected_control(mode="aggress"),
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_control_rejects_invalid_mode(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            server = create_server(routes_path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                request = Request(
                    f"http://{host}:{port}/control",
                    data=json.dumps({"mode": "bogus"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urlopen(request, timeout=2)
                    self.fail("expected HTTP error for invalid mode")
                except HTTPError as exc:
                    self.assertEqual(exc.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_control_accepts_independent_comet_settings(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            control_path = directory_path / ".arena_hero_control.json"
            server = create_server(
                routes_path,
                control_path=control_path,
                port=0,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                request = Request(
                    f"http://{host}:{port}/control",
                    data=json.dumps(
                        {
                            "comet_active": True,
                            "comet_mode": "coordinate",
                            "comet_target": [120, -80],
                            "comet_vanguards": 2,
                            "comet_rangers": 5,
                            "comet_min_reserve_vanguards": 1,
                            "comet_min_reserve_rangers": 2,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    posted = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(
            posted,
            expected_control(
                comet_active=True,
                comet_mode="coordinate",
                comet_target=[120, -80],
                comet_vanguards=2,
                comet_rangers=5,
                comet_min_reserve_vanguards=1,
                comet_min_reserve_rangers=2,
            ),
        )

    def test_core_enemy_bias_and_pursue_beacon_round_trip(self) -> None:
        # 退避三舍 / 趁胜追击 / 御驾亲征 三个布尔开关 POST→GET 往返。
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            control_path = directory_path / ".arena_hero_control.json"
            control_path.write_text("{}", encoding="utf-8")
            server = create_server(
                routes_path, control_path=control_path, port=0
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                request = Request(
                    f"http://{host}:{port}/control",
                    data=json.dumps(
                        {
                            "core_evade_enemies": True,
                            "core_chase_enemies": False,
                            "core_pursue_beacon": True,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    posted = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(
            posted,
            expected_control(
                core_evade_enemies=True,
                core_chase_enemies=False,
                core_pursue_beacon=True,
            ),
        )

    def test_core_enemy_bias_rejects_non_bool(self) -> None:
        # 非布尔值必须拒绝（严格校验，抛 ValueError → 400）。
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            control_path = directory_path / ".arena_hero_control.json"
            control_path.write_text("{}", encoding="utf-8")
            server = create_server(
                routes_path, control_path=control_path, port=0
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                request = Request(
                    f"http://{host}:{port}/control",
                    data=json.dumps({"core_evade_enemies": "yes"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(request, timeout=2)
                self.assertEqual(ctx.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_partial_control_update_preserves_existing_settings(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            control_path = directory_path / ".arena_hero_control.json"
            control_path.write_text(
                json.dumps(
                    {
                        "mode": "aggress",
                        "aggress_vanguards": 6,
                        "aggress_rangers": 7,
                        "comet_active": True,
                        "comet_vanguards": 2,
                        "comet_rangers": 5,
                    }
                ),
                encoding="utf-8",
            )
            server = create_server(
                routes_path,
                control_path=control_path,
                port=0,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                request = Request(
                    f"http://{host}:{port}/control",
                    data=json.dumps({"aggress_rangers": 9}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    posted = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(
            posted,
            expected_control(
                mode="aggress",
                aggress_vanguards=6,
                aggress_rangers=9,
                comet_active=True,
                comet_vanguards=2,
                comet_rangers=5,
            ),
        )

    def test_build_system_revamp_fields_round_trip(self) -> None:
        # 三元比例(含先锋) + 全零囤资源 + 补兵阈值 + 补兵优先级 经 POST→GET 往返。
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            control_path = directory_path / ".arena_hero_control.json"
            control_path.write_text("{}", encoding="utf-8")
            server = create_server(
                routes_path, control_path=control_path, port=0
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                # 三元比例 + 阈值 + 优先级（工人优先）
                request = Request(
                    f"http://{host}:{port}/control",
                    data=json.dumps(
                        {
                            "spawn_ratio": {
                                "ranger": 5, "vanguard": 2, "worker": 1,
                            },
                            "replenish_threshold": {
                                "ranger": 8, "vanguard": 3, "worker": 2,
                            },
                            "replenish_priority": ["worker", "ranger", "vanguard"],
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    posted = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(
            posted["spawn_ratio"],
            {"ranger": 5, "vanguard": 2, "worker": 1},
        )
        self.assertEqual(
            posted["replenish_threshold"],
            {"ranger": 8, "vanguard": 3, "worker": 2},
        )
        self.assertEqual(
            posted["replenish_priority"],
            ["worker", "ranger", "vanguard"],
        )

    def test_build_system_all_zero_ratio_is_allowed(self) -> None:
        # 全零比例 = 停止造兵囤资源，不再被拒绝。
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            control_path = directory_path / ".arena_hero_control.json"
            control_path.write_text("{}", encoding="utf-8")
            server = create_server(
                routes_path, control_path=control_path, port=0
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                request = Request(
                    f"http://{host}:{port}/control",
                    data=json.dumps(
                        {"spawn_ratio": {"ranger": 0, "vanguard": 0, "worker": 0}}
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    posted = json.load(response)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual(
            posted["spawn_ratio"],
            {"ranger": 0, "vanguard": 0, "worker": 0},
        )

    def test_control_rejects_web_page_origin(self) -> None:
        with TemporaryDirectory() as directory:
            routes_path = Path(directory) / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            server = create_server(routes_path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                request = Request(
                    f"http://{host}:{port}/control",
                    data=json.dumps({"mode": "aggress"}).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "https://example.com",
                    },
                    method="POST",
                )
                with self.assertRaises(HTTPError) as raised:
                    urlopen(request, timeout=2)
                self.assertEqual(raised.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_browser_intel_round_trip_is_sanitized(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            intel_path = directory_path / ".arena_hero_browser_intel.json"
            server = create_server(
                routes_path,
                browser_intel_path=intel_path,
                port=0,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            base = f"http://{host}:{port}"
            try:
                request = Request(
                    f"{base}/browser-intel",
                    data=json.dumps(
                        {
                            "version": 99,
                            "source": "page",
                            "captured_at": "2026-08-05T12:00:00+08:00",
                            "resources": [[-64, -168], [-64, -168], [1, True], [2, 3]],
                            "api_key": "must-not-leak",
                        }
                    ).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "Origin": "chrome-extension://overlay-test",
                    },
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    posted = json.load(response)
                with urlopen(f"{base}/browser-intel", timeout=2) as response:
                    loaded = json.load(response)
                stored = json.loads(intel_path.read_text(encoding="utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        expected = {
            "version": 1,
            "source": "browser",
            "captured_at": "2026-08-05T12:00:00+08:00",
            "resources": [[-64, -168], [2, 3]],
        }
        self.assertEqual(posted, expected)
        self.assertEqual(loaded, expected)
        self.assertEqual(stored, expected)
        self.assertNotIn("must-not-leak", json.dumps(posted))

    def test_control_accepts_beacon_mode(self) -> None:
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            routes_path = directory_path / ".arena_hero_routes.json"
            routes_path.write_text("{}", encoding="utf-8")
            server = create_server(routes_path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            try:
                request = Request(
                    f"http://{host}:{port}/control",
                    data=json.dumps({"mode": "beacon"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    posted = json.load(response)
                self.assertEqual(posted, expected_control(mode="beacon"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_manifest_is_read_only_and_scoped(self) -> None:
        extension = Path(__file__).with_name("arena_hero_route_overlay")
        manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest.get("permissions", []), ["storage"])
        self.assertEqual(manifest["host_permissions"], ["http://127.0.0.1:8765/*"])
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in extension.glob("*.js")
        ).lower()
        self.assertNotIn("localstorage", source)
        self.assertNotIn("/api/v1/game/commands", source)
        self.assertNotIn("api.arenahero.io", source)
        self.assertIn("chrome.storage.local", source)
        self.assertIn("alt+shift+r", source)
        self.assertIn("alt+shift+l", source)
        self.assertIn("alt+shift+1", source)
        self.assertIn("/logs", source)
        self.assertIn("showresources", source)
        self.assertIn("showunitlabels", source)
        self.assertIn("officialdialogvisible", source)
        self.assertIn("calculatecontrollayout", source)
        self.assertIn("getboundingclientrect().height", source)
        self.assertIn("2147483000", source)


if __name__ == "__main__":
    unittest.main()
