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
EMPTY_STATS = {
    "tick": 0,
    "mode": "develop",
    "recall": False,
    "resources": 0,
    "capacity": 0,
    "population": 0,
    "workers": 0,
    "vanguards": 0,
    "rangers": 0,
    "core_hp": 0,
    "core_shield": 0,
    "core_state": "RESPAWNING",
    "core_position": None,
    "beacon_position": [0, 0],
    "beacon_status": "UNCLAIMED",
    "visible_enemies": 0,
    "owns_beacon": False,
    "visible_resource_cells": 0,
    "known_resource_cells": 0,
    "known_obstacle_cells": 0,
    "visited_cells": 0,
    "worker_cargo": 0,
    "active_routes": 0,
    "complete_routes": 0,
    "remembered_enemies": 0,
    "exploring_workers": 0,
    "max_worker_search_radius": 0,
    "tick_interval": 0,
    "observed_turns": 0,
    "elapsed_ticks": 0,
    "total_resources_harvested": 0,
    "total_resources_deposited": 0,
    "total_resources_captured": 0,
    "enemy_cores_destroyed": 0,
    "up_time": 0,
    "units_lost": 0,
    "units_built": 0,
    "core_events": 0,
    "harvest_count": 0,
    "deposit_count": 0,
    "shoot_count": 0,
    "move_failures": 0,
    "manual_overrides": 0,
    "event_totals": {},
    "decision_totals": {},
}
VALID_MODES = {"develop", "aggress", "beacon"}
POSITION_STATS = {"core_position", "beacon_position"}
COUNTER_STATS = {"event_totals", "decision_totals"}
SENSITIVE_KEY_PARTS = ("api", "authorization", "credential", "secret", "token")


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


def load_stats(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_stats(data)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return dict(EMPTY_STATS)


def _normalize_counter(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, count in value.items():
        if (
            not isinstance(key, str)
            or len(key) > 128
            or any(part in key.lower() for part in SENSITIVE_KEY_PARTS)
            or isinstance(count, bool)
            or not isinstance(count, int)
        ):
            continue
        result[key] = max(0, int(count))
    return dict(sorted(result.items()))


def _normalize_stats(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return dict(EMPTY_STATS)
    result: dict[str, Any] = {}
    for key, default in EMPTY_STATS.items():
        value = payload.get(key, default)
        if key in COUNTER_STATS:
            result[key] = _normalize_counter(value)
        elif key in POSITION_STATS:
            result[key] = _position(value) if value is not None else None
        elif isinstance(default, bool):
            result[key] = value if isinstance(value, bool) else default
        elif isinstance(default, int):
            result[key] = (
                max(0, int(value))
                if isinstance(value, int) and not isinstance(value, bool)
                else default
            )
        elif isinstance(default, str):
            result[key] = str(value)[:64] if isinstance(value, str) else default
        else:
            result[key] = default
    if result["mode"] not in VALID_MODES:
        result["mode"] = "develop"
    return result


def load_control(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"mode": "develop", "recall": False, "beacon_target_distance": 0}
        mode = data.get("mode", "develop")
        if mode not in VALID_MODES:
            mode = "develop"
        recall = data.get("recall", False)
        raw_distance = data.get("beacon_target_distance", 0)
        distance = (
            max(0, int(raw_distance))
            if isinstance(raw_distance, (int, float))
            and not isinstance(raw_distance, bool)
            else 0
        )
        raw_rally = data.get("rally_point")
        rally = None
        if (
            isinstance(raw_rally, list)
            and len(raw_rally) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw_rally)
        ):
            rally = [int(raw_rally[0]), int(raw_rally[1])]
        result: dict[str, Any] = {
            "mode": mode,
            "recall": bool(recall),
            "beacon_target_distance": distance,
            "rally_point": rally,
        }
        for key in ("aggress_vanguards", "aggress_rangers"):
            raw_value = data.get(key, 0)
            result[key] = (
                max(0, int(raw_value))
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else 0
            )
        return result
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "mode": "develop",
            "recall": False,
            "beacon_target_distance": 0,
            "rally_point": None,
            "aggress_vanguards": 0,
            "aggress_rangers": 0,
        }


def save_control(path: Path, mode: str, recall: bool, data: dict[str, Any] | None = None) -> dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
    payload: dict[str, Any] = {"mode": mode, "recall": bool(recall)}
    if data is not None:
        raw_distance = data.get("beacon_target_distance")
        if isinstance(raw_distance, (int, float)) and not isinstance(
            raw_distance, bool
        ):
            payload["beacon_target_distance"] = max(0, int(raw_distance))
        else:
            payload["beacon_target_distance"] = 0
        raw_rally = data.get("rally_point")
        if (
            isinstance(raw_rally, list)
            and len(raw_rally) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw_rally)
        ):
            payload["rally_point"] = [int(raw_rally[0]), int(raw_rally[1])]
        else:
            payload["rally_point"] = None
        for key in ("aggress_vanguards", "aggress_rangers"):
            raw_value = data.get(key, 0)
            payload[key] = (
                max(0, int(raw_value))
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else 0
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)
    return payload


class RouteOverlayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        routes_path: Path,
        stats_path: Path,
        control_path: Path,
    ) -> None:
        self.routes_path = routes_path
        self.stats_path = stats_path
        self.control_path = control_path
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
        if endpoint == "/stats":
            self._send_json(load_stats(self.server.stats_path), HTTPStatus.OK)
            return
        if endpoint == "/control":
            self._send_json(
                load_control(self.server.control_path),
                HTTPStatus.OK,
            )
            return
        self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        endpoint = self.path.partition("?")[0]
        if endpoint != "/control":
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        origin = self.headers.get("Origin", "")
        if origin and not origin.startswith(("chrome-extension://", "extension://")):
            self._send_json({"error": "forbidden_origin"}, HTTPStatus.FORBIDDEN)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > 4096:
            self._send_json({"error": "payload_too_large"}, HTTPStatus.BAD_REQUEST)
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json({"error": "invalid_json"}, HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(data, dict):
            self._send_json({"error": "invalid_payload"}, HTTPStatus.BAD_REQUEST)
            return
        current = load_control(self.server.control_path)
        mode = data.get("mode", current["mode"])
        recall = data.get("recall", current["recall"])
        try:
            payload = save_control(self.server.control_path, mode, recall, data)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json(payload, HTTPStatus.OK)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(
    routes_path: Path,
    *,
    stats_path: Path | None = None,
    control_path: Path | None = None,
    port: int = DEFAULT_PORT,
) -> RouteOverlayServer:
    return RouteOverlayServer(
        (LOOPBACK_HOST, port),
        routes_path.resolve(),
        (stats_path or routes_path.with_name(".arena_hero_stats.json")).resolve(),
        (control_path or routes_path.with_name(".arena_hero_control.json")).resolve(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Arena Hero read-only route overlay service")
    parser.add_argument(
        "--routes-file",
        type=Path,
        default=Path(".arena_hero_routes.json"),
    )
    parser.add_argument(
        "--stats-file",
        type=Path,
        default=Path(".arena_hero_stats.json"),
    )
    parser.add_argument(
        "--control-file",
        type=Path,
        default=Path(".arena_hero_control.json"),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    server = create_server(
        args.routes_file,
        stats_path=args.stats_file,
        control_path=args.control_file,
        port=args.port,
    )
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
