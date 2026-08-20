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
    "comet_active": False,
    "comet_mode": "beacon",
    "comet_target": None,
    "comet_vanguards": 3,
    "comet_rangers": 3,
    "comet_min_reserve_vanguards": 3,
    "comet_min_reserve_rangers": 3,
    "comet_wounded_threshold": 0.5,
    "comet_rally_enabled": False,
    "comet_rally_distance": 0,
    "comet_selected_vanguards": 0,
    "comet_selected_rangers": 0,
    "comet_retreating": 0,
    "comet_dispatched_tick": 0,
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
VALID_MODES = {"develop", "aggress", "beacon", "migrate", "lightning"}
# 网页控制台新增控制字段（与 arena_hero_strategy.TacticMemory 一致）。
CONTROL_TRANSFER_MODES = {"star", "march", "fortify"}
CONTROL_UNIT_TYPES = ("WORKER", "VANGUARD", "RANGER")
CONTROL_MAX_BUILD_QUEUE_LENGTH = 20
CONTROL_MAX_ORBIT = 900
CONTROL_MAX_WARTIME_RESERVE = 10000
POSITION_STATS = {
    "core_position",
    "beacon_position",
    "comet_target",
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


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _clamp_non_negative_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(0, int(value))


def _normalize_unit_list(raw_units: Any) -> list[dict[str, Any]]:
    """归一化我方/敌方单位列表：保留 id/type/number/position/hp，剥离敏感字段。"""
    if not isinstance(raw_units, list):
        return []
    units: list[dict[str, Any]] = []
    for raw_unit in raw_units[:256]:
        if not isinstance(raw_unit, dict):
            continue
        position = _position(raw_unit.get("position"))
        if position is None:
            continue
        unit_type = raw_unit.get("type")
        if not isinstance(unit_type, str) or not unit_type:
            continue
        entry: dict[str, Any] = {
            "id": str(raw_unit.get("id", ""))[:128],
            "type": unit_type[:32],
            "position": position,
        }
        number = raw_unit.get("number")
        if isinstance(number, int) and not isinstance(number, bool) and number >= 1:
            entry["number"] = number
        hp = raw_unit.get("hp")
        if isinstance(hp, int) and not isinstance(hp, bool) and hp >= 0:
            entry["hp"] = hp
        units.append(entry)
    return units


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
            # 列表/复杂默认值（如 units: []）—— 走下面的显式归一化分支。
            result[key] = default
    # 我方/敌方单位列表：始终走显式归一化（剥离敏感字段、补齐位置/类型）。
    result["units"] = _normalize_unit_list(payload.get("units"))
    result["enemy_units"] = _normalize_unit_list(payload.get("enemy_units"))
    # 透传网页控制台/战况分析需要的非敏感扩展字段。
    for key, value in payload.items():
        if key in result or _is_sensitive_key(key):
            continue
        if isinstance(value, bool):
            result[key] = value
        elif isinstance(value, (int, float)):
            result[key] = _clamp_non_negative_int(value, 0)
        elif isinstance(value, str):
            result[key] = value[:256]
        elif isinstance(value, list):
            result[key] = [
                _position_or_none(item) if isinstance(item, (list, tuple)) else item
                for item in value[:512]
            ]
        elif isinstance(value, dict):
            result[key] = _sanitize_dict(value)
        # 其余类型（None 等）直接丢弃。
    if result["mode"] not in VALID_MODES:
        result["mode"] = "develop"
    return result


def _sanitize_dict(value: Any, *, depth: int = 0) -> Any:
    """递归清理 dict/嵌套结构：剥离敏感 key，整型转非负。"""
    if depth > 6:
        return None
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _is_sensitive_key(key):
                continue
            cleaned[key] = _sanitize_dict(item, depth=depth + 1)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_dict(item, depth=depth + 1) for item in value[:512]]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return _clamp_non_negative_int(value, 0)
    if isinstance(value, str):
        return value[:256]
    return None


def _int_clamp(value: Any, *, minimum: int, maximum: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(minimum, min(maximum, int(value)))


def _position_or_none(value: Any) -> list[int] | None:
    """接受 [x, y]（int/float，非 bool）；其余一律 None。"""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    return [int(value[0]), int(value[1])]


def _default_dashboard_control_fields(payload: dict[str, Any]) -> None:
    """网页控制台新增控制字段的默认值。"""
    payload["core_orbit_radius"] = 0
    payload["core_hold"] = False
    payload["core_target"] = None
    payload["core_transfer_mode"] = "star"
    payload["core_evade_enemies"] = False
    payload["core_chase_enemies"] = False
    payload["core_pursue_beacon"] = False
    payload["build_queue"] = []
    payload["spawn_ratio"] = {"ranger": 1, "vanguard": 1, "worker": 3}
    payload["unit_caps"] = {"worker": 20, "vanguard": 0, "ranger": 0}
    payload["replenish_threshold"] = {"ranger": 0, "vanguard": 0, "worker": 0}
    payload["replenish_priority"] = ["ranger", "worker", "vanguard"]
    payload["wartime_reserve"] = 150
    payload["comet_active"] = False
    payload["comet_mode"] = "beacon"
    payload["comet_target"] = None
    payload["comet_vanguards"] = 3
    payload["comet_rangers"] = 3
    payload["comet_min_reserve_vanguards"] = 3
    payload["comet_min_reserve_rangers"] = 3
    payload["comet_wounded_threshold"] = 0.5
    payload["comet_rally_enabled"] = False
    payload["comet_rally_distance"] = 0


def _read_dashboard_control_fields(data: dict[str, Any], result: dict[str, Any]) -> None:
    """从原始 JSON 读取网页控制台新增字段，做宽松归一化（读取容错）。"""
    result["core_orbit_radius"] = _int_clamp(
        data.get("core_orbit_radius", 0),
        minimum=0,
        maximum=CONTROL_MAX_ORBIT,
        default=0,
    )
    result["core_hold"] = bool(data.get("core_hold", False))
    result["core_target"] = _position_or_none(data.get("core_target"))
    mode = data.get("core_transfer_mode", "star")
    result["core_transfer_mode"] = mode if mode in CONTROL_TRANSFER_MODES else "star"
    result["core_evade_enemies"] = bool(data.get("core_evade_enemies", False))
    result["core_chase_enemies"] = bool(data.get("core_chase_enemies", False))
    result["core_pursue_beacon"] = bool(data.get("core_pursue_beacon", False))
    raw_queue = data.get("build_queue")
    queue: list[str] = []
    if isinstance(raw_queue, list):
        for item in raw_queue[:CONTROL_MAX_BUILD_QUEUE_LENGTH]:
            if isinstance(item, str) and item in CONTROL_UNIT_TYPES:
                queue.append(item)
    result["build_queue"] = queue
    raw_ratio = data.get("spawn_ratio")
    if isinstance(raw_ratio, dict):
        # 三元比例（默认 1:1:3 游侠:先锋:工人）。允许全 0（囤资源）。
        ranger = _int_clamp(raw_ratio.get("ranger", 1), minimum=0, maximum=9999, default=1)
        vanguard = _int_clamp(raw_ratio.get("vanguard", 1), minimum=0, maximum=9999, default=1)
        worker = _int_clamp(raw_ratio.get("worker", 3), minimum=0, maximum=9999, default=3)
        if ranger == 0 and vanguard == 0 and worker == 0:
            result["spawn_ratio"] = {"ranger": 0, "vanguard": 0, "worker": 0}
        else:
            result["spawn_ratio"] = {
                "ranger": ranger,
                "vanguard": vanguard,
                "worker": worker,
            }
    else:
        result["spawn_ratio"] = {"ranger": 1, "vanguard": 1, "worker": 3}
    raw_caps = data.get("unit_caps")
    caps: dict[str, int] = {"worker": 20, "vanguard": 0, "ranger": 0}
    if isinstance(raw_caps, dict):
        for key in caps:
            caps[key] = _int_clamp(raw_caps.get(key, 0), minimum=0, maximum=9999, default=0)
    result["unit_caps"] = caps
    # 补兵阈值：各兵种 < 阈值时优先补。0 = 不主动补该兵种。
    raw_threshold = data.get("replenish_threshold")
    thresholds: dict[str, int] = {"ranger": 0, "vanguard": 0, "worker": 0}
    if isinstance(raw_threshold, dict):
        for key in thresholds:
            thresholds[key] = _int_clamp(
                raw_threshold.get(key, 0), minimum=0, maximum=9999, default=0
            )
    result["replenish_threshold"] = thresholds
    # 补兵优先级：多兵种同时低于阈值时按此顺序补。缺失兵种补到末尾。
    raw_priority = data.get("replenish_priority")
    priority_order: list[str] = []
    if isinstance(raw_priority, list):
        for item in raw_priority:
            if isinstance(item, str) and item.lower() in ("ranger", "vanguard", "worker"):
                key = item.lower()
                if key not in priority_order:
                    priority_order.append(key)
    for key in ("ranger", "worker", "vanguard"):
        if key not in priority_order:
            priority_order.append(key)
    result["replenish_priority"] = priority_order
    result["wartime_reserve"] = _int_clamp(
        data.get("wartime_reserve", 150),
        minimum=0,
        maximum=CONTROL_MAX_WARTIME_RESERVE,
        default=150,
    )
    result["comet_active"] = bool(data.get("comet_active", False))
    comet_mode = data.get("comet_mode", "beacon")
    result["comet_mode"] = (
        comet_mode if comet_mode in ("beacon", "coordinate") else "beacon"
    )
    result["comet_target"] = _position_or_none(data.get("comet_target"))
    result["comet_vanguards"] = _int_clamp(
        data.get("comet_vanguards", 3), minimum=0, maximum=9999, default=3
    )
    result["comet_rangers"] = _int_clamp(
        data.get("comet_rangers", 3), minimum=0, maximum=9999, default=3
    )
    result["comet_min_reserve_vanguards"] = _int_clamp(
        data.get("comet_min_reserve_vanguards", 3), minimum=0, maximum=9999, default=3
    )
    result["comet_min_reserve_rangers"] = _int_clamp(
        data.get("comet_min_reserve_rangers", 3), minimum=0, maximum=9999, default=3
    )
    raw_threshold = data.get("comet_wounded_threshold", 0.5)
    if isinstance(raw_threshold, (int, float)) and not isinstance(raw_threshold, bool):
        result["comet_wounded_threshold"] = max(0.0, min(1.0, float(raw_threshold)))
    else:
        result["comet_wounded_threshold"] = 0.5
    rally_enabled_raw = data.get("comet_rally_enabled")
    if isinstance(rally_enabled_raw, bool):
        result["comet_rally_enabled"] = rally_enabled_raw
    else:
        result["comet_rally_enabled"] = False
    result["comet_rally_distance"] = _int_clamp(
        data.get("comet_rally_distance", 0),
        minimum=0,
        maximum=9999,
        default=0,
    )


def _apply_dashboard_control_fields(data: dict[str, Any], payload: dict[str, Any]) -> None:
    """在 save_control 中把前端 POST 的新增字段写入 payload（严格校验，错误抛 ValueError）。"""
    if "core_orbit_radius" in data:
        value = data["core_orbit_radius"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("core_orbit_radius must be a number")
        payload["core_orbit_radius"] = max(0, min(CONTROL_MAX_ORBIT, int(value)))
    if "core_hold" in data:
        if not isinstance(data["core_hold"], bool):
            raise ValueError("core_hold must be boolean")
        payload["core_hold"] = data["core_hold"]
    if "core_target" in data:
        candidate = _position_or_none(data["core_target"])
        if data["core_target"] is not None and candidate is None:
            raise ValueError("core_target must be null or [x, y]")
        payload["core_target"] = candidate
    if "core_transfer_mode" in data:
        mode = data["core_transfer_mode"]
        if mode not in CONTROL_TRANSFER_MODES:
            raise ValueError(
                f"core_transfer_mode must be one of {sorted(CONTROL_TRANSFER_MODES)}"
            )
        payload["core_transfer_mode"] = mode
    for _flag in ("core_evade_enemies", "core_chase_enemies", "core_pursue_beacon"):
        if _flag in data:
            if not isinstance(data[_flag], bool):
                raise ValueError(f"{_flag} must be boolean")
            payload[_flag] = data[_flag]
    if "build_queue" in data:
        raw_queue = data["build_queue"]
        if not isinstance(raw_queue, list):
            raise ValueError("build_queue must be a list")
        queue: list[str] = []
        for item in raw_queue[:CONTROL_MAX_BUILD_QUEUE_LENGTH]:
            if not isinstance(item, str) or item not in CONTROL_UNIT_TYPES:
                raise ValueError(
                    f"build_queue item must be one of {list(CONTROL_UNIT_TYPES)}"
                )
            queue.append(item)
        payload["build_queue"] = queue
    if "spawn_ratio" in data:
        raw_ratio = data["spawn_ratio"]
        if not isinstance(raw_ratio, dict):
            raise ValueError("spawn_ratio must be an object")
        # 三元比例（默认 1:1:3 游侠:先锋:工人）。允许全 0（停止造兵囤资源）。
        shares: dict[str, int] = {}
        for key, default in (("ranger", 1), ("vanguard", 1), ("worker", 3)):
            value = raw_ratio.get(key, default)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(
                    f"spawn_ratio {key} must be a non-negative number"
                )
            shares[key] = int(value)
        payload["spawn_ratio"] = {
            "ranger": shares["ranger"],
            "vanguard": shares["vanguard"],
            "worker": shares["worker"],
        }
    if "unit_caps" in data:
        raw_caps = data["unit_caps"]
        if not isinstance(raw_caps, dict):
            raise ValueError("unit_caps must be an object")
        caps: dict[str, int] = {}
        for key in ("worker", "vanguard", "ranger"):
            value = raw_caps.get(key, 0)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(f"unit_caps {key} must be a non-negative number")
            caps[key] = int(value)
        payload["unit_caps"] = caps
    if "replenish_threshold" in data:
        raw_threshold = data["replenish_threshold"]
        if not isinstance(raw_threshold, dict):
            raise ValueError("replenish_threshold must be an object")
        thresholds: dict[str, int] = {}
        for key in ("ranger", "vanguard", "worker"):
            value = raw_threshold.get(key, 0)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(
                    f"replenish_threshold {key} must be a non-negative number"
                )
            thresholds[key] = int(value)
        payload["replenish_threshold"] = thresholds
    if "replenish_priority" in data:
        raw_priority = data["replenish_priority"]
        if not isinstance(raw_priority, list):
            raise ValueError("replenish_priority must be a list")
        priority: list[str] = []
        for item in raw_priority:
            if (
                not isinstance(item, str)
                or item.lower() not in ("ranger", "vanguard", "worker")
            ):
                raise ValueError(
                    "replenish_priority items must be ranger/vanguard/worker"
                )
            key = item.lower()
            if key in priority:
                raise ValueError(
                    f"replenish_priority has duplicate: {key}"
                )
            priority.append(key)
        # 缺失的兵种补到末尾，保证三类全覆盖。
        for key in ("ranger", "vanguard", "worker"):
            if key not in priority:
                priority.append(key)
        payload["replenish_priority"] = priority
    if "wartime_reserve" in data:
        value = data["wartime_reserve"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("wartime_reserve must be a number")
        payload["wartime_reserve"] = max(
            0, min(CONTROL_MAX_WARTIME_RESERVE, int(value))
        )
    if "comet_active" in data:
        if not isinstance(data["comet_active"], bool):
            raise ValueError("comet_active must be boolean")
        payload["comet_active"] = data["comet_active"]
    if "comet_mode" in data:
        mode = data["comet_mode"]
        if mode not in ("beacon", "coordinate"):
            raise ValueError("comet_mode must be one of beacon, coordinate")
        payload["comet_mode"] = mode
    if "comet_target" in data:
        candidate = _position_or_none(data["comet_target"])
        if data["comet_target"] is not None and candidate is None:
            raise ValueError("comet_target must be null or [x, y]")
        payload["comet_target"] = candidate
    for key in (
        "comet_vanguards",
        "comet_rangers",
        "comet_min_reserve_vanguards",
        "comet_min_reserve_rangers",
    ):
        if key in data:
            value = data[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{key} must be a number")
            payload[key] = max(0, int(value))
    if "comet_wounded_threshold" in data:
        value = data["comet_wounded_threshold"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("comet_wounded_threshold must be a number")
        payload["comet_wounded_threshold"] = max(0.0, min(1.0, float(value)))
    if "comet_rally_enabled" in data:
        if not isinstance(data["comet_rally_enabled"], bool):
            raise ValueError("comet_rally_enabled must be boolean")
        payload["comet_rally_enabled"] = data["comet_rally_enabled"]
    if "comet_rally_distance" in data:
        value = data["comet_rally_distance"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("comet_rally_distance must be a number")
        payload["comet_rally_distance"] = max(0, int(value))


def load_control(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_control()
        mode = data.get("mode", "develop")
        if mode not in VALID_MODES:
            mode = "develop"
        result: dict[str, Any] = {
            "mode": mode,
        }
        for key in ("aggress_vanguards", "aggress_rangers"):
            raw_value = data.get(key, 0)
            result[key] = (
                max(0, int(raw_value))
                if isinstance(raw_value, (int, float))
                and not isinstance(raw_value, bool)
                else 0
            )
        # === 网页控制台新增控制字段：回显读取值（save_control 做校验/落盘）===
        _read_dashboard_control_fields(data, result)
        return result
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _default_control()


def _default_control() -> dict[str, Any]:
    """控制文件的完整默认值，含网页控制台新增字段。"""
    payload: dict[str, Any] = {
        "mode": "develop",
        "aggress_vanguards": 0,
        "aggress_rangers": 0,
    }
    _default_dashboard_control_fields(payload)
    return payload


def save_control(
    path: Path,
    mode: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
    payload = load_control(path)
    payload.update({"mode": mode})
    if data is not None:
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
        # 网页控制台新增控制字段：严格校验后落盘。
        _apply_dashboard_control_fields(data, payload)
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
        try:
            payload = save_control(self.server.control_path, mode, data)
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
