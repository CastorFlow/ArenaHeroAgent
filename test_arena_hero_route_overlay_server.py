from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import HTTPError, Request, urlopen

from arena_hero_route_overlay_server import create_server


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
                    self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
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
                        "recall": False,
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
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

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
                self.assertEqual(default, {"mode": "develop", "recall": False})

                request = Request(
                    f"{base}/control",
                    data=json.dumps({"mode": "aggress", "recall": True}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2) as response:
                    posted = json.load(response)
                self.assertEqual(posted, {"mode": "aggress", "recall": True})

                with urlopen(f"{base}/control", timeout=2) as response:
                    after = json.load(response)
                self.assertEqual(after, {"mode": "aggress", "recall": True})
                self.assertEqual(
                    json.loads(control_path.read_text(encoding="utf-8")),
                    {"mode": "aggress", "recall": True},
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
        self.assertIn("showresources", source)
        self.assertIn("showunitlabels", source)


if __name__ == "__main__":
    unittest.main()
