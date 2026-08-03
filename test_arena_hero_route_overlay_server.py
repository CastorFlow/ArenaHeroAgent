from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

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
