"""Arena Hero 网页控制台后端。

绑 127.0.0.1:8766（可用 ARENA_HERO_DASHBOARD_PORT 覆盖），配 Nginx 反代 + HTTPS 域名访问。
登录密码读 ARENA_HERO_DASHBOARD_PASSWORD 环境变量或 .dashboard_password 文件（mode 600）；
未配置密码 → 拒绝启动（fail-fast）。除 / 与 /api/login、/api/health 外，所有 /api/* 需
Authorization: Bearer <token>（登录签发，12h 过期）。

数据源全部是代理(arena_hero_tactic.py)落盘的 JSON/JSONL 文件，控制文件
.arena_hero_control.json 是唯一配置入口（代理每 tick 热加载）。本服务只读写这些文件，
不侵入代理主循环。复用 arena_hero_route_overlay_server 的 load_control/save_control/
load_stats/load_logs，保证控制文件 schema 与 Chrome 扩展叠加层一致。

端点：
  GET  /                    → 前端页面 dashboard/index.html
  GET  /api/health          → {status:ok}
  POST /api/login           → {password} → {token}
  GET  /api/config          → 控制文件 + stats 回显的生效值
  POST /api/config          → 原子写控制文件（下次 tick 生效）
  GET  /api/stats           → .arena_hero_stats.json
  GET  /api/telemetry?limit → arena_hero_telemetry.jsonl 尾部
  GET  /api/battle?limit&since → 战况历史尾部（可增量）
  GET  /api/trail?limit&step → Core 轨迹（step 抽稀）
  GET  /api/routes          → .arena_hero_routes.json
  GET  /api/events?limit    → 事件流日志尾部
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from arena_hero_route_overlay_server import (
    load_control,
    load_logs,
    load_routes,
    load_stats,
    save_control,
)

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
PASSWORD_FILE_NAME = ".dashboard_password"
TOKEN_TTL_SECONDS = 12 * 60 * 60
MAX_JSONL_ROWS = 10_000
AUTH_EXEMPT = {"/api/health", "/api/login"}
SENSITIVE_PARTS = ("password", "token", "api", "authorization", "credential", "secret")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

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
        password: str,
    ) -> None:
        self.dashboard_dir = dashboard_dir
        self.control_path = control_path
        self.stats_path = stats_path
        self.telemetry_path = telemetry_path
        self.battle_path = battle_path
        self.trail_path = trail_path
        self.routes_path = routes_path
        self.events_path = events_path
        self._password = password
        self._tokens: dict[str, float] = {}  # token -> expiry unix ts
        self._tokens_lock = threading.Lock()
        super().__init__(address, DashboardHandler)


def _load_password(*, env_value: str | None, password_file: Path) -> str:
    """登录密码：env 优先，其次 .dashboard_password 文件；都没有 → 抛错拒绝启动。"""
    if env_value:
        return env_value
    try:
        raw = password_file.read_text(encoding="utf-8").strip()
    except OSError:
        raise RuntimeError(
            "网页控制台未配置密码：设 ARENA_HERO_DASHBOARD_PASSWORD 或写 "
            f"{password_file}（mode 600，一行明文）。拒绝启动。"
        ) from None
    if not raw:
        raise RuntimeError(
            f"网页控制台密码文件 {password_file} 为空。拒绝启动。"
        )
    return raw


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
            self.server._tokens[token] = expiry  # 滑动续期
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
            self._send_json({"status": "ok"}, HTTPStatus.OK)
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
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:
        # HEAD 与 GET 同路由，_send_bytes 会因 command=="HEAD" 跳过 body。
        self.do_GET()

    def do_POST(self) -> None:
        endpoint = self.path.partition("?")[0]
        if endpoint == "/api/login":
            data = self._read_json_body()
            if data is None:
                return
            provided = data.get("password")
            if not isinstance(provided, str) or not hmac.compare_digest(
                provided, self.server._password
            ):
                self._send_error("invalid_password", HTTPStatus.UNAUTHORIZED)
                return
            token = secrets.token_urlsafe(32)
            with self.server._tokens_lock:
                self.server._tokens[token] = time.time() + TOKEN_TTL_SECONDS
            self._send_json(
                {"token": token, "expires_in": TOKEN_TTL_SECONDS},
                HTTPStatus.OK,
            )
            return
        if endpoint == "/api/config":
            if not self._require_auth():
                return
            data = self._read_json_body()
            if data is None:
                return
            current = load_control(self.server.control_path)
            mode = data.get("mode", current["mode"])
            recall = data.get("recall", current["recall"])
            try:
                payload = save_control(self.server.control_path, mode, recall, data)
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
    password: str | None = None,
    password_file: Path | None = None,
    port: int = DEFAULT_PORT,
) -> DashboardServer:
    resolved_password = _load_password(
        env_value=password,
        password_file=password_file
        or Path(os.environ.get("ARENA_HERO_DASHBOARD_PASSWORD_FILE", PASSWORD_FILE_NAME)),
    )
    return DashboardServer(
        (LOOPBACK_HOST, port),
        dashboard_dir=dashboard_dir,
        control_path=control_path or Path(".arena_hero_control.json"),
        stats_path=stats_path or Path(".arena_hero_stats.json"),
        telemetry_path=telemetry_path or Path("arena_hero_telemetry.jsonl"),
        battle_path=battle_path or Path("arena_hero_battle_history.jsonl"),
        trail_path=trail_path or Path("arena_hero_core_trail.jsonl"),
        routes_path=routes_path or Path(".arena_hero_routes.json"),
        events_path=events_path or Path("arena_hero_events_zh.jsonl"),
        password=resolved_password,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Arena Hero web dashboard")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ARENA_HERO_DASHBOARD_PORT", DEFAULT_PORT)))
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
    parser.add_argument("--password", type=str, default=os.environ.get("ARENA_HERO_DASHBOARD_PASSWORD"))
    parser.add_argument("--password-file", type=Path, default=None)
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
        password=args.password,
        password_file=args.password_file,
        port=args.port,
    )
    print(
        f"Arena Hero dashboard listening on http://{LOOPBACK_HOST}:{args.port} "
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
