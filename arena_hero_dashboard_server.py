"""Arena Hero web dashboard and API-key initialization gateway.

The server listens on ``127.0.0.1:8766`` by default.  The login form accepts the
Arena Hero API key, starts the real tactic with that key, waits until the
official SDK authenticates and publishes the first actionable Turn, then
returns a random dashboard token with a sliding 12-hour idle timeout.  The key
is never written to disk, and expiry of the browser token does not stop Agent.

Use Nginx/Caddy and HTTPS when exposing the dashboard through a domain.  All
data APIs require ``Authorization: Bearer <session token>``.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from arena_hero_agent_supervisor import AgentStartError, AgentSupervisor
from arena_hero_route_overlay_server import (
    load_control,
    load_logs,
    load_routes,
    load_stats,
    save_control,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
TOKEN_TTL_SECONDS = 12 * 60 * 60
MAX_JSONL_ROWS = 10_000
MAX_LOGIN_ATTEMPTS = 8
LOGIN_ATTEMPT_WINDOW_SECONDS = 60.0


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_close(self) -> None:
        self.agent_supervisor.stop()
        super().server_close()

    def __init__(
        self,
        address: tuple[str, int],
        *,
        dashboard_dir: Path,
        control_path: Path,
        stats_path: Path,
        telemetry_path: Path,
        battle_path: Path,
        trail_path: Path,
        routes_path: Path,
        events_path: Path,
        agent_supervisor: AgentSupervisor,
    ) -> None:
        self.dashboard_dir = dashboard_dir
        self.control_path = control_path
        self.stats_path = stats_path
        self.telemetry_path = telemetry_path
        self.battle_path = battle_path
        self.trail_path = trail_path
        self.routes_path = routes_path
        self.events_path = events_path
        self.agent_supervisor = agent_supervisor
        self._tokens: dict[str, float] = {}  # token -> expiry unix ts
        self._tokens_lock = threading.Lock()
        self._login_attempts: dict[str, deque[float]] = {}
        self._login_attempts_lock = threading.Lock()
        super().__init__(address, DashboardHandler)


def _jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit < 1:
        limit = 1
    limit = min(limit, MAX_JSONL_ROWS)
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in raw_lines[-limit:]:
        try:
            parsed = json.loads(raw_line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _trail_rows(path: Path, limit: int, step: int, since: int = 0) -> list[dict[str, Any]]:
    if limit < 1:
        limit = 1
    limit = min(limit, MAX_JSONL_ROWS)
    step = max(1, min(step or 1, limit))
    rows = _jsonl_tail(path, limit * step)
    if since > 0:
        rows = [row for row in rows if row.get("tick", 0) > since]
    if step > 1:
        rows = rows[::step]
    return rows[-limit:]


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    # ---------- helpers ----------
    def _send_bytes(self, body: bytes, status: HTTPStatus, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        # HEAD 请求只回响应头（标正确 Content-Length），不回 body。
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self._send_bytes(body, status, "application/json; charset=utf-8")

    def _send_error(self, message: str, status: HTTPStatus) -> None:
        self._send_json({"error": message}, status)

    def _read_json_body(self, maximum: int = 8192) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > maximum:
            self._send_error("payload_too_large", HTTPStatus.BAD_REQUEST)
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_error("invalid_json", HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(data, dict):
            self._send_error("invalid_payload", HTTPStatus.BAD_REQUEST)
            return None
        return data

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        token = header[len("Bearer "):].strip()
        if not token:
            return False
        with self.server._tokens_lock:
            now = time.time()
            expired = [key for key, expiry in self.server._tokens.items() if expiry < now]
            for key in expired:
                self.server._tokens.pop(key, None)
            expiry = self.server._tokens.get(token)
            if expiry is None or expiry < now:
                return False
            self.server._tokens[token] = now + TOKEN_TTL_SECONDS  # 滑动续期
            return True

    def _require_auth(self) -> bool:
        if not self._authorized():
            self._send_error("unauthorized", HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def _query_int(self, name: str, default: int) -> int:
        query = self.path.partition("?")[2]
        for pair in query.split("&"):
            key, _, value = pair.partition("=")
            if key == name:
                try:
                    return max(0, int(value))
                except ValueError:
                    return default
        return default

    # ---------- routing ----------
    def do_GET(self) -> None:
        endpoint = self.path.partition("?")[0]
        # 静态资源（登录页引用的 echarts 等）必须放开鉴权：浏览器 <script> 无法带
        # Authorization 头，未登录态也要能加载。敏感数据全在 /api/*（登录后才可读）。
        if endpoint.startswith("/vendor/") or endpoint.startswith("/static/"):
            self._serve_static(endpoint.lstrip("/"))
            return
        if endpoint == "/":
            self._serve_frontend()
            return
        if endpoint == "/favicon.ico":
            # 浏览器自动请求，无 token，回 204 避免未登录态出现 401 噪声。
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if endpoint == "/api/health":
            self._send_json(
                {"status": "ok", "agent": self.server.agent_supervisor.status()},
                HTTPStatus.OK,
            )
            return
        if endpoint == "/api/login":
            self._send_error("method_not_allowed", HTTPStatus.METHOD_NOT_ALLOWED)
            return
        if not self._require_auth():
            return
        if endpoint == "/api/config":
            payload = load_control(self.server.control_path)
            effective = load_stats(self.server.stats_path).get(
                "effective_control", {}
            )
            payload["effective"] = effective
            payload["agent"] = self.server.agent_supervisor.status()
            self._send_json(payload, HTTPStatus.OK)
            return
        if endpoint == "/api/stats":
            self._send_json(load_stats(self.server.stats_path), HTTPStatus.OK)
            return
        if endpoint == "/api/telemetry":
            limit = self._query_int("limit", 500)
            rows = _jsonl_tail(self.server.telemetry_path, limit)
            latest_tick = rows[-1]["tick"] if rows else 0
            self._send_json(
                {"version": 1, "latest_tick": latest_tick, "rows": rows},
                HTTPStatus.OK,
            )
            return
        if endpoint == "/api/battle":
            limit = self._query_int("limit", 500)
            since = self._query_int("since", 0)
            rows = _jsonl_tail(self.server.battle_path, limit)
            if since > 0:
                rows = [row for row in rows if row.get("tick", 0) > since]
            latest_tick = max((row.get("tick", 0) for row in rows), default=0)
            self._send_json(
                {"version": 1, "latest_tick": latest_tick, "rows": rows},
                HTTPStatus.OK,
            )
            return
        if endpoint == "/api/trail":
            limit = self._query_int("limit", 500)
            step = self._query_int("step", 1)
            since = self._query_int("since", 0)
            rows = _trail_rows(self.server.trail_path, limit, step, since)
            self._send_json(
                {"version": 1, "rows": rows},
                HTTPStatus.OK,
            )
            return
        if endpoint == "/api/routes":
            self._send_json(load_routes(self.server.routes_path), HTTPStatus.OK)
            return
        if endpoint == "/api/events":
            limit = self._query_int("limit", 200)
            self._send_json(load_logs(self.server.events_path, limit=limit), HTTPStatus.OK)
            return
        self._send_error("not_found", HTTPStatus.NOT_FOUND)

    def _serve_frontend(self) -> None:
        index = self.server.dashboard_dir / "index.html"
        try:
            body = index.read_bytes()
        except OSError:
            self._send_error("frontend_missing", HTTPStatus.NOT_FOUND)
            return
        self._send_bytes(body, HTTPStatus.OK, "text/html; charset=utf-8")

    def _serve_static(self, relative: str) -> None:
        # 仅允许 dashboard 目录内的静态资源，杜绝路径穿越。
        try:
            base = self.server.dashboard_dir.resolve()
            target = (base / relative).resolve()
            if base != target and base not in target.parents:
                self._send_error("forbidden", HTTPStatus.FORBIDDEN)
                return
            body = target.read_bytes()
        except OSError:
            self._send_error("not_found", HTTPStatus.NOT_FOUND)
            return
        content_type = "application/javascript; charset=utf-8" if relative.endswith(
            ".js"
        ) else "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        # HEAD 与 GET 同路由，_send_bytes 会因 command=="HEAD" 跳过 body。
        self.do_GET()

    def _login_rate_key(self) -> str:
        if os.environ.get("ARENA_HERO_DASHBOARD_TRUST_PROXY", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded
        return self.client_address[0] if self.client_address else "unknown"

    def _check_login_rate(self) -> bool:
        key = self._login_rate_key()
        now = time.monotonic()
        with self.server._login_attempts_lock:
            attempts = self.server._login_attempts.setdefault(key, deque())
            while attempts and attempts[0] <= now - LOGIN_ATTEMPT_WINDOW_SECONDS:
                attempts.popleft()
            if len(attempts) >= MAX_LOGIN_ATTEMPTS:
                return False
            attempts.append(now)
            return True

    def _clear_login_rate(self) -> None:
        key = self._login_rate_key()
        with self.server._login_attempts_lock:
            self.server._login_attempts.pop(key, None)

    def do_POST(self) -> None:
        endpoint = self.path.partition("?")[0]
        if endpoint == "/api/login":
            if not self._check_login_rate():
                self._send_json(
                    {"error": "too_many_attempts", "message": "尝试次数过多，请 1 分钟后再试"},
                    HTTPStatus.TOO_MANY_REQUESTS,
                )
                return
            data = self._read_json_body(maximum=16_384)
            if data is None:
                return
            api_key = data.get("api_key")
            if not isinstance(api_key, str):
                self._send_error("invalid_api_key", HTTPStatus.UNAUTHORIZED)
                return
            try:
                agent = self.server.agent_supervisor.start(api_key)
            except AgentStartError as exc:
                if exc.code == "invalid_api_key":
                    status = HTTPStatus.UNAUTHORIZED
                elif exc.code == "agent_already_running":
                    status = HTTPStatus.CONFLICT
                else:
                    status = HTTPStatus.SERVICE_UNAVAILABLE
                self._send_json(
                    {"error": exc.code, "message": str(exc)},
                    status,
                )
                return
            if not self.server.control_path.is_file():
                defaults = load_control(self.server.control_path)
                save_control(self.server.control_path, defaults)
            self._clear_login_rate()
            token = secrets.token_urlsafe(32)
            with self.server._tokens_lock:
                self.server._tokens[token] = time.time() + TOKEN_TTL_SECONDS
            self._send_json(
                {
                    "token": token,
                    "expires_in": TOKEN_TTL_SECONDS,
                    "agent": agent,
                    "config": load_control(self.server.control_path),
                },
                HTTPStatus.OK,
            )
            return
        if endpoint == "/api/config":
            if not self._require_auth():
                return
            data = self._read_json_body()
            if data is None:
                return
            try:
                payload = save_control(self.server.control_path, data)
            except ValueError as exc:
                self._send_error(str(exc), HTTPStatus.BAD_REQUEST)
                return
            self._send_json(payload, HTTPStatus.OK)
            return
        self._send_error("not_found", HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(
    *,
    dashboard_dir: Path = DASHBOARD_DIR,
    control_path: Path | None = None,
    stats_path: Path | None = None,
    telemetry_path: Path | None = None,
    battle_path: Path | None = None,
    trail_path: Path | None = None,
    routes_path: Path | None = None,
    events_path: Path | None = None,
    agent_supervisor: AgentSupervisor | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> DashboardServer:
    # 解析最终路径（缺省用默认），既给 dashboard 读，也透传给 agent 子进程
    # 让 strategy 的 _append_battle_history / _append_core_trail 落盘到同一文件。
    control_path = control_path or Path(".arena_hero_control.json")
    stats_path = stats_path or Path(".arena_hero_stats.json")
    telemetry_path = telemetry_path or Path("arena_hero_telemetry.jsonl")
    battle_path = battle_path or Path("arena_hero_battle_history.jsonl")
    trail_path = trail_path or Path("arena_hero_core_trail.jsonl")
    routes_path = routes_path or Path(".arena_hero_routes.json")
    events_path = events_path or Path("arena_hero_events_zh.jsonl")
    if agent_supervisor is None:
        agent_supervisor = AgentSupervisor()
    # 把 agent 子进程需要写的数据文件路径通过环境变量注入：strategy.py 的
    # battle_history_path / core_trail_path / control_path 都从 env 读，
    # 这样 dashboard 读的文件和 tactic 写的文件是同一份。
    # 无论 supervisor 是这里创建的还是 main() 传入的，都补齐这三项。
    agent_supervisor.extra_env.update({
        "ARENA_HERO_CONTROL_FILE": str(control_path.resolve()),
        "ARENA_HERO_BATTLE_HISTORY_FILE": str(battle_path.resolve()),
        "ARENA_HERO_CORE_TRAIL_FILE": str(trail_path.resolve()),
    })
    return DashboardServer(
        (host, port),
        dashboard_dir=dashboard_dir,
        control_path=control_path,
        stats_path=stats_path,
        telemetry_path=telemetry_path,
        battle_path=battle_path,
        trail_path=trail_path,
        routes_path=routes_path,
        events_path=events_path,
        agent_supervisor=agent_supervisor,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Arena Hero web dashboard")
    parser.add_argument(
        "--host",
        default=os.environ.get("ARENA_HERO_DASHBOARD_HOST", DEFAULT_HOST),
        help="Listen address (default: 127.0.0.1; use a reverse proxy for public HTTPS).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ARENA_HERO_DASHBOARD_PORT", DEFAULT_PORT)),
    )
    parser.add_argument(
        "--dashboard-dir",
        type=Path,
        default=DASHBOARD_DIR,
    )
    parser.add_argument("--control-file", type=Path, default=None)
    parser.add_argument("--stats-file", type=Path, default=None)
    parser.add_argument("--telemetry-file", type=Path, default=None)
    parser.add_argument("--battle-file", type=Path, default=None)
    parser.add_argument("--trail-file", type=Path, default=None)
    parser.add_argument("--routes-file", type=Path, default=None)
    parser.add_argument("--events-file", type=Path, default=None)
    parser.add_argument(
        "--agent-status-file",
        type=Path,
        default=Path(os.environ.get("ARENA_HERO_AGENT_STATUS_FILE", ".arena_hero_agent_status.json")),
    )
    parser.add_argument(
        "--agent-start-timeout",
        type=float,
        default=float(os.environ.get("ARENA_HERO_AGENT_START_TIMEOUT", "25")),
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    server = create_server(
        dashboard_dir=args.dashboard_dir,
        control_path=args.control_file,
        stats_path=args.stats_file,
        telemetry_path=args.telemetry_file,
        battle_path=args.battle_file,
        trail_path=args.trail_file,
        routes_path=args.routes_file,
        events_path=args.events_file,
        agent_supervisor=AgentSupervisor(
            status_path=args.agent_status_file,
            startup_timeout=args.agent_start_timeout,
            # 战况历史 / Core 轨迹路径在 create_server 解析后注入；
            # 这里先创建 supervisor 占位，路径补丁由 create_server 在
            # extra_env 中补齐（见 create_server 的 agent_supervisor 分支）。
        ),
        host=args.host,
        port=args.port,
    )
    print(
        f"Arena Hero dashboard listening on http://{args.host}:{args.port} "
        f"(dashboard={args.dashboard_dir})",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
