from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from uuid import UUID

from arena_hero import (
    BeaconStatus,
    CoreView,
    ResolutionEvent,
    Turn,
    UnitType,
    UnitView,
)


LOG_VERSION = 1
DEFAULT_LOG_PATH = Path("arena_hero_events_zh.jsonl")
MAX_LOG_BYTES = 2_000_000
MAX_LOG_LINES = 2_000
SEEN_EVENT_LIMIT = 4_000
SENSITIVE_KEY_PARTS = ("api", "authorization", "credential", "secret", "token")

UNIT_TYPE_LABELS = {
    "WORKER": "工人",
    "VANGUARD": "先锋",
    "RANGER": "游侠",
}
REASON_LABELS = {
    "ALREADY_CARRIED": "信标已被携带",
    "ATTACK": "遭受攻击",
    "BEACON_NOT_PRESENT": "当前位置没有信标",
    "CARGO_FULL": "工人货舱已满",
    "CELL_UNIT_LIMIT": "目标格已达到容量上限",
    "CORE_ALREADY_MOVING": "Core 已在迁移",
    "CORE_DESTINATION_OCCUPIED": "Core 目标格被占用",
    "CORE_DESTINATION_OUT_OF_BOUNDS": "Core 目标超出坐标范围",
    "CORE_DESTINATION_TERRAIN_BLOCKED": "Core 目标格被地形阻挡",
    "CORE_MOVING": "Core 正在迁移",
    "CORE_NOT_MOVING": "Core 当前没有迁移",
    "CORE_NOT_PRESENT": "Core 不在当前位置",
    "CORE_RESOURCE_FULL": "Core 资源仓已满",
    "DETERMINISTIC_ID_COLLISION": "单位编号生成冲突",
    "HP_FULL": "生命值已满",
    "INSUFFICIENT_RESOURCES": "资源不足",
    "MOVE_BLOCKED_TERRAIN": "前方有障碍",
    "MOVE_CONTESTED": "目标格发生移动争夺",
    "MOVE_DEPENDENCY_FAILED": "前方单位未能离开",
    "MOVE_DESTINATION_OCCUPIED": "目标格被敌方占用",
    "MOVE_OUT_OF_BOUNDS": "移动超出坐标范围",
    "MOVE_SWAP_BLOCKED": "与敌方换位失败",
    "NO_LEGAL_SPAWN": "没有合法重生位置",
    "NOT_AT_OWN_CORE": "单位不在己方 Core 位置",
    "NOT_BEACON_CARRIER": "当前单位没有携带信标",
    "NOT_RESOURCE_CELL": "当前位置没有资源",
    "RESOURCE_DEPLETED": "资源已被其他工人采走",
    "SELF_DESTRUCT": "主动自毁",
    "SHIELD_FULL": "护盾已满",
    "SHOT_MISSED": "射击未命中",
    "UNIT": "敌方单位",
    "UPKEEP_DEFICIT": "维护费不足",
    "WORKER_EMPTY": "工人没有携带资源",
}


def _position_text(position: tuple[int, int] | None) -> str:
    return f"[{position[0]}, {position[1]}]" if position is not None else "未知位置"


def _int_value(values: Mapping[str, Any], key: str, default: int = 0) -> int:
    value = values.get(key, default)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, UUID):
        return str(value)[:8]
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:32]:
            name = str(key)[:64]
            if any(part in name.lower() for part in SENSITIVE_KEY_PARTS):
                continue
            result[name] = _safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:32]]
    return str(value)[:256]


def _event_category(event_type: str) -> str:
    if event_type.startswith("BEACON_"):
        return "信标"
    if event_type.startswith(("SHOT_", "SWEEP_", "UNIT_DAMAGED", "DESTRUCTION_")):
        return "战斗"
    if event_type.startswith(("HARVEST_", "DEPOSIT_", "WORKER_CARGO_")):
        return "资源"
    if event_type in {
        "UPKEEP_PAID",
        "CORE_RESOURCES_CAPTURED",
        "CORE_RESOURCE_OVERFLOW_DESTROYED",
    }:
        return "资源"
    if event_type.startswith("CORE_SPAWN_"):
        return "生产"
    if event_type.startswith(("UNIT_MOVE_", "CORE_MOVE_")):
        return "移动"
    if event_type.startswith("CORE_") or event_type == "RESPAWN_DELAYED":
        return "Core"
    if event_type.startswith("UNIT_"):
        return "单位"
    return "系统"


def _event_level(event: ResolutionEvent) -> str:
    event_type = event.event_type
    values = event.values or {}
    if event_type in {"CORE_DESTROYED", "BEACON_DROPPED_ON_DEATH"}:
        return "danger"
    if event_type == "UNIT_DAMAGED" and _int_value(values, "hp") <= 0:
        return "danger"
    if event_type.endswith("_FAILED") or event_type in {
        "UNIT_DAMAGED",
        "CORE_DAMAGED",
        "CORE_RESOURCE_OVERFLOW_DESTROYED",
        "RESPAWN_DELAYED",
    }:
        return "warning"
    if event_type in {"SHOT_MISSED", "UNIT_MOVE_SUCCEEDED", "CORE_MOVE_PROGRESS"}:
        return "debug"
    if event_type.endswith("_SUCCEEDED") or event_type in {
        "SHOT_HIT",
        "CORE_RESOURCES_CAPTURED",
        "DESTRUCTION_PARTICIPATION",
        "BEACON_PICKED_UP",
    }:
        return "success"
    return "info"


def _event_copy(event: ResolutionEvent) -> tuple[dict[str, Any], tuple[int, int] | None]:
    values = event.values or {}
    position = event.position
    return values, position


def _event_text(
    event: ResolutionEvent,
    entity_name: Callable[[UUID | None, str], str],
) -> tuple[str, str]:
    event_type = event.event_type
    reason = REASON_LABELS.get(event.reason_code or "", event.reason_code or "")
    values, position = _event_copy(event)
    place = _position_text(position)
    actor = entity_name(event.actor_id, "执行者")
    target = entity_name(event.target_id, "目标")
    amount = event.resource_amount or _int_value(values, "amount")

    if event_type == "UNIT_SELF_DESTRUCTED":
        return "单位自毁", f"{actor} 在 {place} 主动自毁"
    if event_type == "WORKER_CARGO_DROPPED":
        return "工人货物掉落", f"{actor} 在 {place} 掉落 {amount} 点资源"
    if event_type in {"UNIT_HEAL_SUCCEEDED", "CORE_HEAL_SUCCEEDED"}:
        hp = _int_value(values, "hp")
        return "治疗成功", f"{actor} 恢复 {amount} 点生命，当前生命 {hp}"
    if event_type in {"UNIT_HEAL_FAILED", "CORE_HEAL_FAILED"}:
        return "治疗失败", f"{actor} 治疗失败：{reason or '未知原因'}"
    if event_type == "UPKEEP_PAID":
        due = _int_value(values, "due")
        paid = _int_value(values, "paid")
        deficit = _int_value(values, "deficit")
        return "支付维护费", f"应付 {due}，已支付 {paid}，缺口 {deficit}"
    if event_type == "CORE_DAMAGED":
        damage = _int_value(values, "damage")
        shield = _int_value(values, "shield_damage")
        hp = _int_value(values, "hp_damage")
        return "Core 遭到攻击", f"Core 在 {place} 受到 {damage} 点伤害（盾 {shield} / 生命 {hp}）"
    if event_type == "CORE_DESTROYED":
        cause = reason or "未知原因"
        return "Core 被摧毁", f"Core 在 {place} 被摧毁，原因：{cause}"
    if event_type == "CORE_RESOURCE_OVERFLOW_DESTROYED":
        capacity = _int_value(values, "capacity")
        return "资源溢出损失", f"人口下降导致 {amount} 点资源被销毁，当前容量 {capacity}"
    if event_type == "CORE_RESOURCES_CAPTURED":
        available = _int_value(values, "available")
        destroyed = _int_value(values, "destroyed")
        return "掠夺敌方资源", f"从被摧毁的敌方 Core 获得 {amount}/{available} 点资源，溢出损失 {destroyed}"
    if event_type == "CORE_ACTION_FAILED":
        return "Core 操作失败", f"Core 操作失败：{reason or '未知原因'}"
    if event_type == "CORE_REPAIR_SUCCEEDED":
        return "护盾修复", f"Core 护盾修复至 {_int_value(values, 'shield')}"
    if event_type == "CORE_REPAIR_FAILED":
        return "护盾修复失败", f"Core 护盾修复失败：{reason or '未知原因'}"
    if event_type == "CORE_SPAWN_SUCCEEDED":
        raw_type = str(values.get("unit_type", "UNIT"))
        unit_type = UNIT_TYPE_LABELS.get(raw_type, raw_type)
        return "生产单位", f"Core 在 {place} 生产 {unit_type}，消耗 {_int_value(values, 'cost')} 点资源"
    if event_type == "CORE_SPAWN_FAILED":
        required = _int_value(values, "required")
        detail = f"，需要 {required} 点资源" if required else ""
        return "生产失败", f"Core 生产失败：{reason or '未知原因'}{detail}"
    if event_type == "DEPOSIT_SUCCEEDED":
        remaining = _int_value(values, "remaining")
        return "资源入仓", f"{actor} 向 Core 提交 {amount} 点资源，剩余携带 {remaining}"
    if event_type == "DEPOSIT_FAILED":
        return "资源入仓失败", f"{actor} 在 {place} 提交失败：{reason or '未知原因'}"
    if event_type == "HARVEST_SUCCEEDED":
        source = values.get("source")
        source_text = "掉落资源" if source == "DROPPED_CARGO" else "矿点"
        return "采集资源", f"{actor} 在 {place} 从{source_text}采集 {amount} 点资源"
    if event_type == "HARVEST_FAILED":
        return "采集失败", f"{actor} 在 {place} 采集失败：{reason or '未知原因'}"
    if event_type == "BEACON_HARVEST_BONUS":
        return "信标采集加成", f"{actor} 获得 {amount} 点额外采集资源"
    if event_type == "SWEEP_RESOLVED":
        hits = _int_value(values, "targets_hit")
        return "先锋横扫", f"{actor} 横扫 {place}，命中 {hits} 个目标"
    if event_type == "SHOT_HIT":
        damage = _int_value(values, "damage")
        return "游侠命中", f"{actor} 在 {place} 命中 {target}，造成 {damage} 点伤害"
    if event_type == "SHOT_MISSED":
        return "游侠射失", f"{actor} 射击 {place} 未命中"
    if event_type == "UNIT_DAMAGED":
        damage = _int_value(values, "damage")
        hp = _int_value(values, "hp")
        if hp <= 0:
            return "单位阵亡", f"{target} 在 {place} 受到 {damage} 点伤害并阵亡（{reason or '未知原因'}）"
        return "单位受伤", f"{target} 在 {place} 受到 {damage} 点伤害，剩余生命 {hp}（{reason or '未知原因'}）"
    if event_type == "DESTRUCTION_PARTICIPATION":
        object_name = "敌方 Core" if event.reason_code == "CORE" else "敌方单位"
        return "确认击毁", f"我方参与摧毁 {object_name}，位置 {place}"
    if event_type == "UNIT_MOVE_FAILED":
        return "单位移动失败", f"{actor} 在 {place} 移动失败：{reason or '未知原因'}"
    if event_type == "CORE_MOVE_STARTED":
        destination = values.get("destination")
        destination_text = (
            _position_text((int(destination[0]), int(destination[1])))
            if isinstance(destination, (list, tuple)) and len(destination) == 2
            else "未知位置"
        )
        return "Core 开始迁移", f"Core 从 {place} 开始向 {destination_text} 迁移"
    if event_type == "CORE_MOVE_PROGRESS":
        return "Core 迁移中", f"Core 迁移进度 {_int_value(values, 'progress')}/{_int_value(values, 'required')}"
    if event_type == "CORE_MOVE_SUCCEEDED":
        return "Core 迁移完成", f"Core 已迁移至 {place}"
    if event_type in {"CORE_MOVE_FAILED", "CORE_MOVE_START_FAILED"}:
        return "Core 迁移失败", f"Core 在 {place} 迁移失败：{reason or '未知原因'}"
    if event_type == "CORE_MOVE_CANCELLED":
        return "Core 取消迁移", f"Core 在 {place} 取消迁移"
    if event_type == "BEACON_PICKED_UP":
        return "取得冠军信标", f"{actor} 在 {place} 取得冠军信标"
    if event_type in {"BEACON_DROPPED", "BEACON_DROPPED_ON_DEATH"}:
        cause = "携带者阵亡" if event_type.endswith("ON_DEATH") else "主动放下"
        return "冠军信标掉落", f"冠军信标因{cause}掉落在 {place}"
    if event_type == "BEACON_PICKUP_FAILED":
        return "拾取信标失败", f"{actor} 拾取信标失败：{reason or '未知原因'}"
    if event_type == "BEACON_DROP_FAILED":
        return "放下信标失败", f"{actor} 放下信标失败：{reason or '未知原因'}"
    if event_type == "RESPAWN_DELAYED":
        return "重生延迟", f"暂时无法找到合法重生位置：{reason or '未知原因'}"
    if event_type == "CORE_RESPAWNED":
        return "Core 已重生", f"Core 在 {place} 重生，初始资源 {_int_value(values, 'resources')}"

    detail = f"，原因：{reason}" if reason else ""
    return "未识别事件", f"发生事件 {event_type}{detail}，位置 {place}"


def format_resolution_event(
    event: ResolutionEvent,
    entity_name: Callable[[UUID | None, str], str],
    *,
    recorded_at: str | None = None,
) -> dict[str, Any] | None:
    if event.event_type == "UNIT_MOVE_SUCCEEDED":
        return None
    if event.event_type == "CORE_MOVE_PROGRESS":
        return None
    if event.event_type == "SWEEP_RESOLVED" and _int_value(event.values or {}, "targets_hit") <= 0:
        return None
    title, message = _event_text(event, entity_name)
    return {
        "version": LOG_VERSION,
        "recorded_at": recorded_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "tick": int(event.tick),
        "event_id": str(event.event_id),
        "source": "server",
        "category": _event_category(event.event_type),
        "level": _event_level(event),
        "title": title,
        "message": message,
        "event_type": event.event_type[:96],
        "reason_code": (event.reason_code or "")[:96] or None,
        "position": list(event.position) if event.position is not None else None,
        "actor": entity_name(event.actor_id, "执行者") if event.actor_id else None,
        "target": entity_name(event.target_id, "目标") if event.target_id else None,
        "values": _safe_value(event.values or {}),
    }


class ChineseEventLogger:
    def __init__(self, path: Path = DEFAULT_LOG_PATH) -> None:
        self.path = path
        self._seen_order: deque[str] = deque(maxlen=SEEN_EVENT_LIMIT)
        self._seen: set[str] = set()
        self._last_visible_enemies: int | None = None
        self._visible_enemy_ids: set[str] = set()
        self._enemy_core_owners: dict[str, str] = {}
        self._last_mode: str | None = None
        self._last_owns_beacon: bool | None = None
        self._load_seen_ids()

    def _load_seen_ids(self) -> None:
        if not self.path.is_file():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-SEEN_EVENT_LIMIT:]
        except OSError:
            return
        for line in lines:
            try:
                record = json.loads(line)
                event_id = record.get("event_id")
            except (TypeError, AttributeError, json.JSONDecodeError):
                continue
            if isinstance(event_id, str):
                self._remember(event_id)
            values = record.get("values")
            if not isinstance(values, dict) or values.get("object_type") != "CORE":
                continue
            object_id = values.get("object_id")
            owner_username = values.get("owner_username")
            if isinstance(object_id, str) and isinstance(owner_username, str):
                self._enemy_core_owners[object_id] = owner_username

    def _remember(self, event_id: str) -> None:
        if event_id in self._seen:
            return
        if len(self._seen_order) == self._seen_order.maxlen:
            expired = self._seen_order.popleft()
            self._seen.discard(expired)
        self._seen_order.append(event_id)
        self._seen.add(event_id)

    def _entity_resolver(
        self,
        turn: Turn,
        unit_labels: Mapping[str, Any],
    ) -> Callable[[UUID | None, str], str]:
        objects: dict[UUID, Any] = {unit.id: unit for unit in turn.units}
        objects.update({enemy.id: enemy for enemy in turn.visible_enemies})
        if turn.core is not None:
            objects[turn.core.id] = turn.core

        def resolve(object_id: UUID | None, fallback: str) -> str:
            if object_id is None:
                return fallback
            label = unit_labels.get(str(object_id))
            if label is not None:
                unit_type = UNIT_TYPE_LABELS.get(
                    str(getattr(label, "object_type", "UNIT")),
                    "单位",
                )
                number = getattr(label, "number", None)
                if isinstance(number, int) and number > 0:
                    return f"{unit_type}#{number}"
            obj = objects.get(object_id)
            if obj is not None:
                view = getattr(obj, "view", obj)
                if isinstance(view, CoreView):
                    return "我方 Core" if view.controlled else f"敌方 Core @{view.owner_username}"
                unit_type = getattr(view, "unit_type", None)
                if isinstance(unit_type, UnitType):
                    prefix = "我方" if getattr(view, "controlled", True) else "敌方"
                    return f"{prefix}{UNIT_TYPE_LABELS.get(unit_type.value, '单位')}"
            owner_username = self._enemy_core_owners.get(str(object_id))
            if owner_username is not None:
                return f"敌方 Core @{owner_username}"
            return f"{fallback} {str(object_id)[:8]}"

        return resolve

    def append_turn(
        self,
        turn: Turn,
        unit_labels: Mapping[str, Any],
        *,
        mode: str,
    ) -> None:
        records: list[dict[str, Any]] = []
        for enemy in turn.visible_enemies:
            if isinstance(enemy, CoreView):
                self._enemy_core_owners[str(enemy.id)] = enemy.owner_username
        resolver = self._entity_resolver(turn, unit_labels)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for event in turn.events:
            event_id = str(event.event_id)
            if event_id in self._seen:
                continue
            record = format_resolution_event(event, resolver, recorded_at=now)
            self._remember(event_id)
            if record is not None:
                records.append(record)

        visible_enemies = len(turn.visible_enemies)
        current_enemy_ids = {str(enemy.id) for enemy in turn.visible_enemies}
        for enemy in turn.visible_enemies:
            enemy_id = str(enemy.id)
            if enemy_id in self._visible_enemy_ids:
                continue
            if isinstance(enemy, CoreView):
                owner_username = enemy.owner_username
                records.append(
                    self._state_record(
                        turn.tick,
                        "发现敌方 Core",
                        (
                            f"发现敌方 Core @{owner_username}，账号：@{owner_username}，"
                            f"Core ID：{enemy_id}，位置 {_position_text(enemy.position)}，"
                            f"生命 {enemy.hp}，护盾 {enemy.shield}"
                        ),
                        "战斗",
                        "warning",
                        f"enemy_core_spotted:{enemy_id}",
                        now,
                        position=enemy.position,
                        event_type="ENEMY_CORE_SPOTTED",
                        target=f"敌方 Core @{owner_username}",
                        values={
                            "object_id": enemy_id,
                            "object_type": "CORE",
                            "owner_username": owner_username,
                            "owner_display": f"@{owner_username}",
                            "identity_scope": "public_core_owner",
                            "hp": enemy.hp,
                            "shield": enemy.shield,
                        },
                    )
                )
            elif isinstance(enemy, UnitView):
                unit_type = UNIT_TYPE_LABELS.get(enemy.unit_type.value, "单位")
                records.append(
                    self._state_record(
                        turn.tick,
                        "发现敌方单位",
                        (
                            f"发现敌方{unit_type}，单位 ID：{enemy_id}，"
                            f"位置 {_position_text(enemy.position)}，生命 {enemy.hp}；"
                            "官方未公开敌方单位的所属账号"
                        ),
                        "战斗",
                        "warning",
                        f"enemy_unit_spotted:{enemy_id}",
                        now,
                        position=enemy.position,
                        event_type="ENEMY_UNIT_SPOTTED",
                        target=f"敌方{unit_type}",
                        values={
                            "object_id": enemy_id,
                            "object_type": enemy.unit_type.value,
                            "owner_username": None,
                            "identity_scope": "private_unit_owner",
                            "hp": enemy.hp,
                        },
                    )
                )
        self._visible_enemy_ids = current_enemy_ids
        if self._last_visible_enemies is not None:
            if self._last_visible_enemies == 0 and visible_enemies > 0:
                records.append(
                    self._state_record(
                        turn.tick,
                        "发现敌情",
                        f"视野内发现 {visible_enemies} 个敌方目标",
                        "战斗",
                        "warning",
                        "enemy_spotted",
                        now,
                    )
                )
            elif self._last_visible_enemies > 0 and visible_enemies == 0:
                records.append(
                    self._state_record(
                        turn.tick,
                        "敌情解除",
                        "当前视野内已没有敌方目标",
                        "战斗",
                        "info",
                        "enemy_cleared",
                        now,
                    )
                )
        self._last_visible_enemies = visible_enemies

        mode_labels = {"develop": "发育", "aggress": "侵略", "beacon": "抢信标"}
        if self._last_mode is None:
            records.append(
                self._state_record(
                    turn.tick,
                    "日志系统已启动",
                    f"开始记录 Arena Hero 事件，当前为{mode_labels.get(mode, mode)}模式",
                    "系统",
                    "info",
                    "logger_started",
                    now,
                    position=turn.core.position if turn.core is not None else None,
                )
            )
        elif mode != self._last_mode:
            records.append(
                self._state_record(
                    turn.tick,
                    "策略模式切换",
                    f"策略已切换为{mode_labels.get(mode, mode)}模式",
                    "系统",
                    "info",
                    f"mode:{mode}",
                    now,
                )
            )
        self._last_mode = mode

        owned_ids = {unit.id for unit in turn.units}
        if turn.core is not None:
            owned_ids.add(turn.core.id)
        owns_beacon = (
            turn.beacon.status is BeaconStatus.CARRIED
            and turn.beacon.carrier_id in owned_ids
        )
        if self._last_owns_beacon is not None and owns_beacon != self._last_owns_beacon:
            records.append(
                self._state_record(
                    turn.tick,
                    "信标状态变化",
                    "我方已持有冠军信标" if owns_beacon else "我方已失去冠军信标",
                    "信标",
                    "success" if owns_beacon else "danger",
                    "beacon_owned" if owns_beacon else "beacon_lost",
                    now,
                    position=turn.beacon.position,
                )
            )
        self._last_owns_beacon = owns_beacon
        self._append(records)

    def append_client_error(self, tick: int, error: str) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        self._append(
            [
                self._state_record(
                    tick,
                    "计划提交失败",
                    f"Agent 计划未被接受：{str(error)[:160]}",
                    "系统",
                    "danger",
                    "submit_failed",
                    now,
                )
            ]
        )

    def _state_record(
        self,
        tick: int,
        title: str,
        message: str,
        category: str,
        level: str,
        suffix: str,
        recorded_at: str,
        *,
        position: tuple[int, int] | None = None,
        event_type: str | None = None,
        target: str | None = None,
        values: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "version": LOG_VERSION,
            "recorded_at": recorded_at,
            "tick": int(tick),
            "event_id": f"state:{tick}:{suffix}",
            "source": "state",
            "category": category,
            "level": level,
            "title": title,
            "message": message,
            "event_type": event_type or suffix.upper(),
            "reason_code": None,
            "position": list(position) if position is not None else None,
            "actor": None,
            "target": target,
            "values": _safe_value(values or {}),
        }

    def _append(self, records: Iterable[dict[str, Any]]) -> None:
        items = list(records)
        if not items:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        try:
            with self.path.open("a", encoding="utf-8") as stream:
                for record in items:
                    stream.write(
                        json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
        except OSError:
            return

    def _rotate_if_needed(self) -> None:
        try:
            if not self.path.is_file() or self.path.stat().st_size <= MAX_LOG_BYTES:
                return
            lines = self.path.read_text(encoding="utf-8").splitlines()
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                "\n".join(lines[-MAX_LOG_LINES:]) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError:
            return
