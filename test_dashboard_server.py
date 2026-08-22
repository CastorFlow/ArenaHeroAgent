from __future__ import annotations

import json
import threading
import unittest
from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from arena_hero_agent_supervisor import AgentStartError
from arena_hero_dashboard_server import (
    DASHBOARD_DIR,
    MAX_LOGIN_ATTEMPTS,
    create_server,
)


VALID_KEY = "valid-test-key"


class FakeAgentSupervisor:
    def __init__(self) -> None:
        self.key: str | None = None
        self.started = 0
        self.stopped = 0
        # 真实 AgentSupervisor 有 extra_env，create_server 会往里写数据文件路径。
        self.extra_env: dict[str, str] = {}

    def start(self, api_key: str) -> dict[str, object]:
        self.started += 1
        if api_key != VALID_KEY:
            raise AgentStartError("invalid_api_key", "API Key 无效或已停用")
        self.key = api_key
        return {
            "reused": self.started > 1,
            "state": "running",
            "running": True,
            "pid": 12345,
            "last_error": None,
        }

    def status(self) -> dict[str, object]:
        if self.key is None:
            return {"state": "idle", "running": False, "pid": None, "last_error": None}
        return {
            "state": "running",
            "running": True,
            "pid": 12345,
            "last_error": None,
        }

    def stop(self) -> None:
        self.stopped += 1
        self.key = None


class DashboardServerTests(unittest.TestCase):
    def make_server(self, directory: Path, supervisor: FakeAgentSupervisor):
        server = create_server(
            dashboard_dir=DASHBOARD_DIR,
            control_path=directory / ".arena_hero_control.json",
            stats_path=directory / ".arena_hero_stats.json",
            telemetry_path=directory / "arena_hero_telemetry.jsonl",
            battle_path=directory / "arena_hero_battle_history.jsonl",
            trail_path=directory / "arena_hero_core_trail.jsonl",
            routes_path=directory / ".arena_hero_routes.json",
            events_path=directory / "arena_hero_events_zh.jsonl",
            agent_supervisor=supervisor,
            host="127.0.0.1",
            port=0,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(cleanup)
        host, port = server.server_address
        return server, f"http://{host}:{port}"

    def request(self, url: str, *, method="GET", payload=None, headers=None, expect_json=True):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=body, headers=headers or {}, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=5) as response:
                content = json.load(response) if expect_json else response.read()
                return response.status, dict(response.headers), content
        except HTTPError as exc:
            with exc:
                return exc.code, dict(exc.headers), json.load(exc)

    def test_health_is_open_and_data_requires_auth(self) -> None:
        supervisor = FakeAgentSupervisor()
        with TemporaryDirectory() as directory:
            _, base = self.make_server(Path(directory), supervisor)
            status, _, payload = self.request(f"{base}/api/health")
            self.assertEqual(status, HTTPStatus.OK)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["agent"]["state"], "idle")

            status, _, payload = self.request(f"{base}/api/config")
            self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
            self.assertEqual(payload["error"], "unauthorized")

    def test_api_key_login_initializes_defaults_and_config_round_trip(self) -> None:
        supervisor = FakeAgentSupervisor()
        with TemporaryDirectory() as directory:
            directory_path = Path(directory)
            _, base = self.make_server(directory_path, supervisor)
            status, _, payload = self.request(
                f"{base}/api/login", method="POST", payload={"api_key": VALID_KEY}
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertTrue(payload["token"])
            self.assertEqual(payload["agent"]["state"], "running")
            self.assertNotIn("mode", payload["config"])
            self.assertEqual(payload["config"]["spawn_ratio"]["worker"], 3)
            self.assertNotIn(VALID_KEY, json.dumps(payload))
            self.assertTrue((directory_path / ".arena_hero_control.json").is_file())

            token = payload["token"]
            headers = {"Authorization": f"Bearer {token}"}
            status, _, current = self.request(
                f"{base}/api/config", headers=headers
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertNotIn("mode", current)

            status, _, changed = self.request(
                f"{base}/api/config",
                method="POST",
                payload={"core_hold": True},
                headers=headers,
            )
            self.assertEqual(status, HTTPStatus.OK)
            self.assertTrue(changed["core_hold"])
            self.assertNotIn("mode", changed)
        self.assertEqual(supervisor.started, 1)

    def test_legacy_password_is_rejected_and_invalid_key_never_echoes(self) -> None:
        supervisor = FakeAgentSupervisor()
        with TemporaryDirectory() as directory:
            _, base = self.make_server(Path(directory), supervisor)
            for key in (VALID_KEY + "-wrong", "", 123):
                status, _, payload = self.request(
                    f"{base}/api/login", method="POST", payload={"api_key": key}
                )
                self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
                self.assertEqual(payload["error"], "invalid_api_key")
                if key:
                    self.assertNotIn(str(key), json.dumps(payload))
            status, _, payload = self.request(
                f"{base}/api/login", method="POST", payload={"password": VALID_KEY}
            )
            self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
            self.assertEqual(payload["error"], "invalid_api_key")
        self.assertEqual(supervisor.started, 2)
        self.assertFalse(supervisor.key)

    def test_login_rate_limit(self) -> None:
        supervisor = FakeAgentSupervisor()
        with TemporaryDirectory() as directory:
            _, base = self.make_server(Path(directory), supervisor)
            for _ in range(MAX_LOGIN_ATTEMPTS):
                status, _, _ = self.request(
                    f"{base}/api/login",
                    method="POST",
                    payload={"api_key": "wrong-key"},
                )
                self.assertEqual(status, HTTPStatus.UNAUTHORIZED)
            status, _, payload = self.request(
                f"{base}/api/login",
                method="POST",
                payload={"api_key": VALID_KEY},
            )
            self.assertEqual(status, HTTPStatus.TOO_MANY_REQUESTS)
            self.assertEqual(payload["error"], "too_many_attempts")

    def test_static_is_available_without_login_and_traversal_is_rejected(self) -> None:
        supervisor = FakeAgentSupervisor()
        with TemporaryDirectory() as directory:
            _, base = self.make_server(Path(directory), supervisor)
            status, _, body = self.request(f"{base}/vendor/echarts.min.js", expect_json=False)
            self.assertGreater(len(body), 100_000)
            self.assertEqual(status, HTTPStatus.OK)
            status, _, payload = self.request(
                f"{base}/vendor/%2e%2e/arena_hero_tactic.py"
            )
            self.assertIn(status, {HTTPStatus.FORBIDDEN, HTTPStatus.NOT_FOUND})
            self.assertEqual(set(payload), {"error"})


if __name__ == "__main__":
    unittest.main()
