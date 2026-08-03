from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
EMPTY_ROUTES = {
    "version": 2,
    "tick": 0,
    "routes": [],
    "units": [],
    "resources": [],
}


def _position(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        return None
    return [int(value[0]), int(value[1])]


def _unit_number(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return int(value)


def _normalize_routes(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return dict(EMPTY_ROUTES)
    tick = payload.get("tick", 0)
    if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
        tick = 0

    routes: list[dict[str, Any]] = []
    raw_routes = payload.get("routes", [])
    if not isinstance(raw_routes, list):
        raw_routes = []
    for raw_route in raw_routes[:256]:
        if not isinstance(raw_route, dict):
            continue
        object_id = raw_route.get("object_id")
        object_type = raw_route.get("object_type")
        number = _unit_number(raw_route.get("number"))
        start = _position(raw_route.get("start"))
        goal_value = raw_route.get("goal")
        goal = _position(goal_value) if goal_value is not None else None
        raw_path = raw_route.get("path")
        if (
            not isinstance(object_id, str)
            or not isinstance(object_type, str)
            or start is None
            or not isinstance(raw_path, list)
        ):
            continue
        path = [position for item in raw_path[:4096] if (position := _position(item))]
        if not path or path[0] != start:
            continue
        routes.append(
            {
                "object_id": object_id[:128],
                "object_type": object_type[:32],
                "number": number,
                "start": start,
                "goal": goal,
                "path": path,
                "reason": str(raw_route.get("reason", ""))[:160],
                "complete": raw_route.get("complete") is True,
            }
        )

    units: list[dict[str, Any]] = []
    raw_units = payload.get("units", [])
    if not isinstance(raw_units, list):
        raw_units = []
    for raw_unit in raw_units[:256]:
        if not isinstance(raw_unit, dict):
            continue
        object_id = raw_unit.get("object_id")
        object_type = raw_unit.get("object_type")
        number = _unit_number(raw_unit.get("number"))
        position = _position(raw_unit.get("position"))
        if (
            not isinstance(object_id, str)
            or not isinstance(object_type, str)
            or number is None
            or position is None
        ):
            continue
        units.append(
            {
                "object_id": object_id[:128],
                "object_type": object_type[:32],
                "number": number,
                "position": position,
            }
        )

    resources: list[list[int]] = []
    raw_resources = payload.get("resources", [])
    if isinstance(raw_resources, list):
        resources = [
            position
            for value in raw_resources[:4096]
            if (position := _position(value)) is not None
        ]

    return {
        "version": 2,
        "tick": tick,
        "routes": routes,
        "units": units,
        "resources": resources,
    }


def load_routes(path: Path) -> dict[str, Any]:
    try:
        return _normalize_routes(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return dict(EMPTY_ROUTES)


class RouteOverlayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], routes_path: Path) -> None:
        self.routes_path = routes_path
        super().__init__(address, RouteOverlayHandler)


class RouteOverlayHandler(BaseHTTPRequestHandler):
    server: RouteOverlayServer

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        endpoint = self.path.partition("?")[0]
        if endpoint == "/health":
            self._send_json({"status": "ok"}, HTTPStatus.OK)
            return
        if endpoint == "/routes":
            self._send_json(load_routes(self.server.routes_path), HTTPStatus.OK)
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(
    routes_path: Path,
    *,
    port: int = DEFAULT_PORT,
) -> RouteOverlayServer:
    return RouteOverlayServer((LOOPBACK_HOST, port), routes_path.resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description="Arena Hero read-only route overlay service")
    parser.add_argument(
        "--routes-file",
        type=Path,
        default=Path(".arena_hero_routes.json"),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    server = create_server(args.routes_file, port=args.port)
    print(
        f"Arena Hero route overlay listening on http://{LOOPBACK_HOST}:{args.port}",
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
