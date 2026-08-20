from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from arena_hero_agent_supervisor import AgentStartError, AgentSupervisor


VALID_KEY = "valid-supervisor-key"
FAKE_CHILD = dedent(
    """
    import json, os, time
    from pathlib import Path

    key = os.environ.get("ARENA_HERO_API_KEY", "")
    status_path = Path(os.environ["ARENA_HERO_AGENT_STATUS_FILE"])
    session_id = os.environ.get("ARENA_HERO_AGENT_SESSION_ID", "")
    if os.environ.get("FAKE_CHILD_MODE") == "hang":
        time.sleep(60)
    state = "running" if key == %r else "auth_failed"
    status_path.write_text(json.dumps({
        "version": 1,
        "state": state,
        "session_id": session_id,
        "updated_at": time.time(),
    }), encoding="utf-8")
    if state == "running":
        (Path.cwd() / "supervisor-cwd.txt").write_text("ok", encoding="utf-8")
        time.sleep(60)
    time.sleep(2)
    """
    % VALID_KEY
)


class AgentSupervisorTests(unittest.TestCase):
    def make_supervisor(self, directory: Path, *, startup_timeout=3.0, extra_env=None):
        agent_path = directory / "fake_agent.py"
        agent_path.write_text(FAKE_CHILD, encoding="utf-8")
        return AgentSupervisor(
            agent_path=agent_path,
            status_path=directory / "status.json",
            stdout_path=directory / "agent.log",
            stderr_path=directory / "agent_err.log",
            python_executable=sys.executable,
            startup_timeout=startup_timeout,
            extra_env=extra_env,
        )

    def test_valid_key_starts_once_reuses_same_key_and_rejects_key_change(self) -> None:
        with TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            supervisor = self.make_supervisor(directory)
            self.addCleanup(supervisor.stop)
            first = supervisor.start("  " + VALID_KEY + "  ")
            second = supervisor.start(VALID_KEY)
            self.assertTrue(first["running"])
            self.assertFalse(first["reused"])
            self.assertTrue(second["reused"])
            self.assertEqual(first["pid"], second["pid"])

            with self.assertRaises(AgentStartError) as raised:
                supervisor.start(VALID_KEY + "-different")
            self.assertEqual(raised.exception.code, "agent_already_running")
            self.assertTrue(supervisor.status()["running"])
            self.assertEqual(
                (directory / "supervisor-cwd.txt").read_text(encoding="utf-8"),
                "ok",
            )

            supervisor.stop()
            stopped = supervisor.status()
            self.assertFalse(stopped["running"])
            self.assertEqual(stopped["state"], "stopped")
            self.assertIsNone(stopped["pid"])

            status_text = (directory / "status.json").read_text(encoding="utf-8")
            self.assertNotIn(VALID_KEY, status_text)
            for log_name in ("agent.log", "agent_err.log"):
                log_text = (directory / log_name).read_text(encoding="utf-8")
                self.assertNotIn(VALID_KEY, log_text)

    def test_invalid_api_key_is_reported_as_invalid(self) -> None:
        with TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            supervisor = self.make_supervisor(directory)
            self.addCleanup(supervisor.stop)
            with self.assertRaises(AgentStartError) as raised:
                supervisor.start("definitely-invalid")
            self.assertEqual(raised.exception.code, "invalid_api_key")
            self.assertEqual(supervisor.status()["state"], "auth_failed")
            self.assertFalse(supervisor.status()["running"])
            self.assertEqual(
                json.loads((directory / "status.json").read_text(encoding="utf-8"))[
                    "state"
                ],
                "auth_failed",
            )

    def test_startup_timeout_stops_child(self) -> None:
        with TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            supervisor = self.make_supervisor(
                directory,
                startup_timeout=1.0,
                extra_env={"FAKE_CHILD_MODE": "hang"},
            )
            with self.assertRaises(AgentStartError) as raised:
                supervisor.start(VALID_KEY)
            self.assertEqual(raised.exception.code, "startup_timeout")
            self.assertFalse(supervisor.status()["running"])
            self.assertEqual(supervisor.status()["state"], "failed")

    def test_empty_or_oversized_key_is_rejected_without_child_start(self) -> None:
        with TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            supervisor = self.make_supervisor(directory)
            for key in ("", " ", "x" * 4097):
                with self.assertRaises(AgentStartError) as raised:
                    supervisor.start(key)
                self.assertEqual(raised.exception.code, "invalid_api_key")
            self.assertEqual(supervisor.status()["state"], "idle")
            self.assertFalse(supervisor.status()["running"])


if __name__ == "__main__":
    unittest.main()
