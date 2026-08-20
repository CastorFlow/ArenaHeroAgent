"""Start the Arena Hero tactic only after a dashboard API-key login succeeds.

The API key is passed to the child process through its environment and is never
written to a repository file.  The tactic reports a small, credential-free
startup status file after the official SDK has authenticated and delivered the
first actionable Turn.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


DEFAULT_STATUS_FILE = Path(".arena_hero_agent_status.json")
DEFAULT_STDOUT_FILE = None
DEFAULT_STDERR_FILE = None
DEFAULT_START_TIMEOUT = 25.0


class AgentStartError(RuntimeError):
    """A safe, user-facing Agent startup failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _read_status(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class AgentSupervisor:
    """Own one tactic child process for one dashboard deployment."""

    def __init__(
        self,
        *,
        agent_path: Path | None = None,
        status_path: Path = DEFAULT_STATUS_FILE,
        stdout_path: Path | None = DEFAULT_STDOUT_FILE,
        stderr_path: Path | None = DEFAULT_STDERR_FILE,
        python_executable: str = sys.executable,
        startup_timeout: float = DEFAULT_START_TIMEOUT,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        root = Path(__file__).resolve().parent
        self.agent_path = (agent_path or root / "arena_hero_tactic.py").resolve()
        self.status_path = status_path.resolve()
        self.stdout_path = (stdout_path or root / "agent.log").resolve()
        self.stderr_path = (stderr_path or root / "agent_err.log").resolve()
        self.python_executable = python_executable
        self.startup_timeout = max(1.0, float(startup_timeout))
        self.extra_env = dict(extra_env or {})
        self._process: subprocess.Popen[bytes] | None = None
        self._key_fingerprint: bytes | None = None
        self._fingerprint_secret = os.urandom(32)
        self._state = "idle"
        self._last_error: str | None = None
        self._lock = threading.RLock()

    def _fingerprint(self, api_key: str) -> bytes:
        return hmac.new(
            self._fingerprint_secret,
            api_key.encode("utf-8"),
            hashlib.sha256,
        ).digest()

    def _is_running_unlocked(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _stop_unlocked(self) -> None:
        process = self._process
        self._process = None
        self._key_fingerprint = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def start(self, api_key: str) -> dict[str, Any]:
        """Authenticate by starting the real tactic and waiting for its first Turn."""

        api_key = api_key.strip()
        if not api_key:
            raise AgentStartError("invalid_api_key", "API Key 不能为空")
        if len(api_key) > 4096:
            raise AgentStartError("invalid_api_key", "API Key 格式无效")
        fingerprint = self._fingerprint(api_key)

        with self._lock:
            if self._is_running_unlocked() and self._key_fingerprint is not None:
                if hmac.compare_digest(fingerprint, self._key_fingerprint):
                    self._state = "running"
                    self._last_error = None
                    return {"reused": True, **self.status()}
                raise AgentStartError(
                    "agent_already_running",
                    "已有 Agent 正在运行；如需更换 API Key，请先重启控制台服务",
                )

            self._state = "starting"
            self._last_error = None
            session_id = uuid4().hex
            try:
                self.status_path.unlink(missing_ok=True)
            except OSError as exc:
                self._state = "failed"
                self._last_error = "无法准备 Agent 状态文件"
                raise AgentStartError("startup_failed", self._last_error) from exc

            self.stdout_path.parent.mkdir(parents=True, exist_ok=True)
            self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env.update(self.extra_env)
            env["ARENA_HERO_API_KEY"] = api_key
            env["ARENA_HERO_AGENT_STATUS_FILE"] = str(self.status_path)
            env["ARENA_HERO_AGENT_SESSION_ID"] = session_id

            try:
                with self.stdout_path.open("ab") as stdout, self.stderr_path.open("ab") as stderr:
                    self._process = subprocess.Popen(
                        [self.python_executable, str(self.agent_path)],
                        cwd=str(self.agent_path.parent),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                    )
            except OSError as exc:
                self._process = None
                self._state = "failed"
                self._last_error = "无法启动后台 Agent"
                raise AgentStartError("startup_failed", self._last_error) from exc
            finally:
                # Do not retain the plaintext credential in the parent environment map.
                env.pop("ARENA_HERO_API_KEY", None)

            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                status = _read_status(self.status_path)
                if status.get("session_id") == session_id:
                    reported_state = status.get("state")
                    if reported_state == "running":
                        self._key_fingerprint = fingerprint
                        self._state = "running"
                        self._last_error = None
                        return {"reused": False, **self.status()}
                    if reported_state == "auth_failed":
                        self._stop_unlocked()
                        self._state = "auth_failed"
                        self._last_error = "API Key 无效或已停用"
                        raise AgentStartError("invalid_api_key", self._last_error)
                    if reported_state in {"protocol_error", "transport_error", "failed"}:
                        self._stop_unlocked()
                        self._state = "failed"
                        messages = {
                            "protocol_error": "Arena Hero SDK 与服务端协议不兼容",
                            "transport_error": "暂时无法连接 Arena Hero 服务",
                            "failed": "Agent 初始化失败",
                        }
                        self._last_error = messages[reported_state]
                        raise AgentStartError(reported_state, self._last_error)

                if self._process is not None and self._process.poll() is not None:
                    return_code = self._process.returncode
                    self._process = None
                    self._state = "failed"
                    if return_code == 2:
                        self._last_error = "API Key 无效或已停用"
                        raise AgentStartError("invalid_api_key", self._last_error)
                    self._last_error = "Agent 在初始化完成前退出"
                    raise AgentStartError("startup_failed", self._last_error)
                time.sleep(0.1)

            self._stop_unlocked()
            self._state = "failed"
            self._last_error = "验证超时，请检查 Arena Hero 服务或网络后重试"
            raise AgentStartError("startup_timeout", self._last_error)

    def status(self) -> dict[str, Any]:
        """Return credential-free process state."""

        with self._lock:
            process = self._process
            running = process is not None and process.poll() is None
            if not running and self._state == "running":
                self._state = "stopped"
                self._key_fingerprint = None
            return {
                "state": self._state,
                "running": running,
                "pid": process.pid if running else None,
                "last_error": self._last_error,
            }

    def stop(self) -> None:
        with self._lock:
            self._stop_unlocked()
            self._state = "stopped"
