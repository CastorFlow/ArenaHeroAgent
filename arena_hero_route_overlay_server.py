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
    "raid_enabled": False,
    "raid_recall": False,
    "raid_vanguards": 1,
    "raid_rangers": 2,
    "raid_selected_vanguards": 0,
    "raid_selected_rangers": 0,
    "raid_core_position": None,
    "raid_core_acquired_tick": 0,
    "raid_sweep_radius": 18,
    "migration_candidate": None,
    "migration_target": None,
    "migration_site_checked": False,
    "migration_site_score": 0,
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
    "core_threat_count": 0,
    "core_reinforcement_active": False,
    "owns_beacon": False,
    "visible_resource_cells": 0,
    "known_resource_cells": 0,
    "browser_resource_hints": 0,
    "browser_intel_age_seconds": 0,
    "browser_intel_online": False,
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
EMPTY_LOGS = {
    "version": 1,
    "latest_tick": 0,
    "entries": [],
}
EMPTY_BROWSER_INTEL = {
    "version": 1,
    "source": "browser",
    "captured_at": None,
    "resources": [],
}
VALID_MODES = {"develop", "aggress", "beacon", "migrate"}
POSITION_STATS = {
    "core_position",
    "beacon_position",
    "migration_candidate",
    "migration_target",
    "raid_core_position",
}
COUNTER_STATS = {"event_totals", "decision_totals"}
SENSITIVE_KEY_PARTS = ("api", "authorization", "credential", "secret", "token")
LOG_LEVELS = {"debug", "info", "success", "warning", "danger"}


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


def _normalize_browser_intel(payload: Any) -> dict[str, Any]:
    """Normalize browser-only map hints without treating them as game truth."""
    if not isinstance(payload, dict):
        return dict(EMPTY_BROWSER_INTEL)
    captured_at = payload.get("captured_at")
    if not isinstance(captured_at, str) or not captured_at.strip():
        captured_at = None
    else:
        captured_at = captured_at[:64]
    resources: list[list[int]] = []
    seen: set[tuple[int, int]] = set()
    raw_resources = payload.get("resources", [])
    if isinstance(raw_resources, list):
        for value in raw_resources[:4096]:
            position = _position(value)
            if position is None:
                continue
            key = (position[0], position[1])
            if key in seen:
                continue
            seen.add(key)
            resources.append(position)
    resources.sort(key=lambda position: (position[0], position[1]))
    return {
        "version": 1,
        "source": "browser",
        "captured_at": captured_at,
        "resources": resources,
    }


def load_routes(path: Path) -> dict[str, Any]:
    try:
        return _normalize_routes(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return dict(EMPTY_ROUTES)


def load_browser_intel(path: Path) -> dict[str, Any]:
    try:
        return _normalize_browser_intel(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return dict(EMPTY_BROWSER_INTEL)


def load_stats(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _normalize_stats(data)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return dict(EMPTY_STATS)


def _log_text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:maximum]


def _normalize_log_entry(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    tick = payload.get("tick")
    event_id = _log_text(payload.get("event_id"), 160)
    title = _log_text(payload.get("title"), 96)
    message = _log_text(payload.get("message"), 512)
    if (
        isinstance(tick, bool)
        or not isinstance(tick, int)
        or tick < 0
        or event_id is None
        or title is None
        or message is None
    ):
        return None
    level = payload.get("level", "info")
    if level not in LOG_LEVELS:
        level = "info"
    return {
        "version": 1,
        "recorded_at": _log_text(payload.get("recorded_at"), 48),
        "tick": tick,
        "event_id": event_id,
        "source": _log_text(payload.get("source"), 24) or "server",
        "category": _log_text(payload.get("category"), 32) or "系统",
        "level": level,
        "title": title,
        "message": message,
        "event_type": _log_text(payload.get("event_type"), 96),
        "reason_code": _log_text(payload.get("reason_code"), 96),
        "position": _position(payload.get("position")),
        "actor": _log_text(payload.get("actor"), 96),
        "target": _log_text(payload.get("target"), 96),
    }


def load_logs(path: Path, *, limit: int = 250) -> dict[str, Any]:
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return dict(EMPTY_LOGS)
    entries: list[dict[str, Any]] = []
    for raw_line in raw_lines[-max(1, min(limit, 500)) :]:
        try:
            raw_entry = json.loads(raw_line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        entry = _normalize_log_entry(raw_entry)
        if entry is not None:
            entries.append(entry)
    return {
        "version": 1,
        "latest_tick": max((entry["tick"] for entry in entries), default=0),
        "entries": entries,
    }


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
            return {
                "mode": "develop",
                "recall": False,
                "raid_enabled": False,
                "raid_recall": False,
                "raid_vanguards": 1,
                "raid_rangers": 2,
                "beacon_target_distance": 0,
            }
        mode = data.get("mode", "develop")
        if mode not in VALID_MODES:
            mode = "develop"
        recall = data.get("recall", False)
        raid_enabled = data.get("raid_enabled", False)
        raid_recall = data.get("raid_recall", False)
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
            "raid_enabled": bool(raid_enabled),
            "raid_recall": bool(raid_recall),
            "beacon_target_distance": distance,
            "rally_point": rally,
        }
        if "migration_candidate" in data or "auto_migrate" in data:
            result["migration_candidate"] = _position(
                data.get("migration_candidate")
            )
            result["auto_migrate"] = bool(data.get("auto_migrate", False))
        for key in ("aggress_vanguards", "aggress_rangers"):
            raw_value = data.get(key, 0)
            result[key] = (
                max(0, int(raw_value))
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else 0
            )
        for key, default in (("raid_vanguards", 1), ("raid_rangers", 2)):
            raw_value = data.get(key, default)
            result[key] = (
                max(0, int(raw_value))
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else default
            )
        return result
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {
            "mode": "develop",
            "recall": False,
            "raid_enabled": False,
            "raid_recall": False,
            "raid_vanguards": 1,
            "raid_rangers": 2,
            "beacon_target_distance": 0,
            "rally_point": None,
            "aggress_vanguards": 0,
            "aggress_rangers": 0,
        }


def save_control(
    path: Path,
    mode: str,
    recall: bool,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
    payload = load_control(path)
    payload.update({"mode": mode, "recall": bool(recall)})
    if data is not None:
        if "beacon_target_distance" in data:
            raw_distance = data["beacon_target_distance"]
            if not isinstance(raw_distance, (int, float)) or isinstance(
                raw_distance,
                bool,
            ):
                raise ValueError("beacon_target_distance must be a number")
            payload["beacon_target_distance"] = max(0, int(raw_distance))
        if "rally_point" in data:
            raw_rally = data["rally_point"]
            if raw_rally is None:
                payload["rally_point"] = None
            elif (
                isinstance(raw_rally, list)
                and len(raw_rally) == 2
                and all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    for value in raw_rally
                )
            ):
                payload["rally_point"] = [int(raw_rally[0]), int(raw_rally[1])]
            else:
                raise ValueError("rally_point must be null or [x, y]")
        if "migration_candidate" in data:
            raw_candidate = data["migration_candidate"]
            if raw_candidate is None:
                payload["migration_candidate"] = None
            else:
                candidate = _position(raw_candidate)
                if candidate is None:
                    raise ValueError("migration_candidate must be null or [x, y]")
                payload["migration_candidate"] = candidate
        if "auto_migrate" in data:
            if not isinstance(data["auto_migrate"], bool):
                raise ValueError("auto_migrate must be boolean")
            payload["auto_migrate"] = data["auto_migrate"]
        for key in ("raid_enabled", "raid_recall"):
            if key not in data:
                continue
            if not isinstance(data[key], bool):
                raise ValueError(f"{key} must be boolean")
            payload[key] = data[key]
        for key in ("aggress_vanguards", "aggress_rangers"):
            if key not in data:
                continue
            raw_value = data[key]
            if not isinstance(raw_value, (int, float)) or isinstance(
                raw_value,
                bool,
            ):
                raise ValueError(f"{key} must be a number")
            payload[key] = max(0, int(raw_value))
        for key in ("raid_vanguards", "raid_rangers"):
            if key not in data:
                continue
            raw_value = data[key]
            if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
                raise ValueError(f"{key} must be a number")
            payload[key] = max(0, int(raw_value))
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
        logs_path: Path,
        browser_intel_path: Path,
    ) -> None:
        self.routes_path = routes_path
        self.stats_path = stats_path
        self.control_path = control_path
        self.logs_path = logs_path
        self.browser_intel_path = browser_intel_path
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
        origin = self.headers.get("Origin", "")
        if origin.startswith(("chrome-extension://", "extension://")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
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
        if endpoint == "/logs":
            self._send_json(load_logs(self.server.logs_path), HTTPStatus.OK)
            return
        if endpoint == "/browser-intel":
            self._send_json(
                load_browser_intel(self.server.browser_intel_path),
                HTTPStatus.OK,
            )
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
        if endpoint not in {"/control", "/browser-intel"}:
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
        if length > (65536 if endpoint == "/browser-intel" else 4096):
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
        if endpoint == "/browser-intel":
            payload = _normalize_browser_intel(data)
            path = self.server.browser_intel_path
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
            self._send_json(payload, HTTPStatus.OK)
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
        origin = self.headers.get("Origin", "")
        if not origin.startswith(("chrome-extension://", "extension://")):
            self._send_json({"error": "forbidden_origin"}, HTTPStatus.FORBIDDEN)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(
    routes_path: Path,
    *,
    stats_path: Path | None = None,
    control_path: Path | None = None,
    logs_path: Path | None = None,
    browser_intel_path: Path | None = None,
    port: int = DEFAULT_PORT,
) -> RouteOverlayServer:
    return RouteOverlayServer(
        (LOOPBACK_HOST, port),
        routes_path.resolve(),
        (stats_path or routes_path.with_name(".arena_hero_stats.json")).resolve(),
        (control_path or routes_path.with_name(".arena_hero_control.json")).resolve(),
        (logs_path or routes_path.with_name("arena_hero_events_zh.jsonl")).resolve(),
        (browser_intel_path or routes_path.with_name(".arena_hero_browser_intel.json")).resolve(),
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
    parser.add_argument(
        "--logs-file",
        type=Path,
        default=Path("arena_hero_events_zh.jsonl"),
    )
    parser.add_argument(
        "--browser-intel-file",
        type=Path,
        default=Path(".arena_hero_browser_intel.json"),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    server = create_server(
        args.routes_file,
        stats_path=args.stats_file,
        control_path=args.control_file,
        logs_path=args.logs_file,
        browser_intel_path=args.browser_intel_file,
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
