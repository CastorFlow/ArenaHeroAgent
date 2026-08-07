from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import traceback
from getpass import getpass
from pathlib import Path
from types import ModuleType

from arena_hero import (
    APIError,
    ArenaHeroClient,
    AuthenticationError,
    PolicyViolationError,
    ProtocolError,
    TransportError,
    Turn,
    TurnClosedError,
)

from arena_hero_event_log import ChineseEventLogger, DEFAULT_LOG_PATH
import arena_hero_strategy as strategy_module


DecisionSummary = strategy_module.DecisionSummary
TacticMemory = strategy_module.TacticMemory


def _load_strategy_candidate(path: Path, version: int) -> ModuleType:
    """Load a changed strategy without mutating the currently working module."""

    module_name = f"_arena_hero_strategy_hot_{version}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load strategy from {path}")
    candidate = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = candidate
    try:
        spec.loader.exec_module(candidate)
        candidate.TacticMemory
        candidate.SmartTactic
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return candidate


def choose_actions(turn: Turn, memory: TacticMemory | None = None) -> DecisionSummary:
    """Compatibility wrapper used by tests and one-off decision callers."""

    return strategy_module.SmartTactic(memory).choose_actions(turn)


def _read_dotenv_key(path: Path) -> str | None:
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "ARENA_HERO_API_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value or None
    return None


def load_api_key() -> str:
    key = os.environ.get("ARENA_HERO_API_KEY") or _read_dotenv_key(Path(".env"))
    if key:
        return key
    if sys.stdin.isatty():
        key = getpass("Arena Hero API key: ").strip()
        if key:
            return key
    raise RuntimeError("Set ARENA_HERO_API_KEY or add it to .env before live play.")


def _append_telemetry(
    path: Path,
    summary: DecisionSummary,
    *,
    accepted: bool,
    error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > 2_000_000:
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(lines[-2_000:]) + "\n", encoding="utf-8")
    record = {
        "tick": summary.tick,
        "accepted": accepted,
        "error": error,
        "resources": summary.resources,
        "resource_capacity": summary.resource_capacity,
        "population": summary.population,
        "visible_enemies": summary.visible_enemies,
        "unit_actions": summary.unit_actions,
        "core_action": summary.has_core_action,
        "previous_events": summary.previous_events,
        "decisions": summary.decisions,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def play(
    api_key: str,
    *,
    base_url: str = "https://api.arenahero.io",
    websocket_url: str | None = None,
    max_turns: int | None = None,
    memory_path: Path = Path(".arena_hero_memory.json"),
    telemetry_path: Path = Path("arena_hero_telemetry.jsonl"),
    stats_path: Path = Path(".arena_hero_stats.json"),
    event_log_path: Path = DEFAULT_LOG_PATH,
) -> None:
    completed_turns = 0
    strategy = strategy_module
    strategy_file = Path(strategy.__file__ or "arena_hero_strategy.py")
    strategy_mtime = strategy_file.stat().st_mtime_ns
    pending_strategy_mtime: int | None = None
    memory = strategy.TacticMemory.load(memory_path)
    tactic = strategy.SmartTactic(memory)
    fallback_strategy: ModuleType | None = None
    event_logger = ChineseEventLogger(event_log_path)

    with ArenaHeroClient(
        api_key=api_key,
        base_url=base_url,
        websocket_url=websocket_url,
    ) as game:
        for turn in game.turns():
            current_mtime = strategy_file.stat().st_mtime_ns
            if current_mtime != strategy_mtime:
                if pending_strategy_mtime != current_mtime:
                    pending_strategy_mtime = current_mtime
                    print(f"tick={turn.tick} strategy_reload_pending=True", flush=True)
                else:
                    try:
                        memory.save(memory_path)
                        candidate = _load_strategy_candidate(
                            strategy_file,
                            current_mtime,
                        )
                        candidate_memory = candidate.TacticMemory.load(memory_path)
                        candidate_tactic = candidate.SmartTactic(candidate_memory)
                    except Exception:
                        strategy_mtime = current_mtime
                        pending_strategy_mtime = None
                        print(
                            f"tick={turn.tick} strategy_reload_failed=True",
                            file=sys.stderr,
                            flush=True,
                        )
                        traceback.print_exc()
                    else:
                        fallback_strategy = strategy
                        strategy = candidate
                        strategy_file = Path(strategy.__file__ or strategy_file)
                        strategy_mtime = current_mtime
                        pending_strategy_mtime = None
                        memory = candidate_memory
                        tactic = candidate_tactic
                        print(f"tick={turn.tick} strategy_reloaded=True", flush=True)
            else:
                pending_strategy_mtime = None
            previous_labels = dict(memory.unit_labels)
            try:
                summary = tactic.choose_actions(turn)
            except TurnClosedError as exc:
                print(
                    f"tick={turn.tick} skipped={type(exc).__name__}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            except Exception:
                print(
                    f"tick={turn.tick} strategy_runtime_failed=True",
                    file=sys.stderr,
                    flush=True,
                )
                traceback.print_exc()
                if fallback_strategy is not None:
                    strategy = fallback_strategy
                    fallback_strategy = None
                    memory = strategy.TacticMemory.load(memory_path)
                    tactic = strategy.SmartTactic(memory)
                    print(
                        f"tick={turn.tick} strategy_rolled_back=True",
                        file=sys.stderr,
                        flush=True,
                    )
                continue
            log_labels = {**previous_labels, **memory.unit_labels}
            try:
                accepted = turn.submit()
            except TurnClosedError as exc:
                error = type(exc).__name__
                event_logger.append_turn(turn, log_labels, mode=memory.mode)
                _append_telemetry(
                    telemetry_path,
                    summary,
                    accepted=False,
                    error=error,
                )
                print(
                    f"tick={turn.tick} skipped={error}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            except APIError as exc:
                event_logger.append_turn(turn, log_labels, mode=memory.mode)
                event_logger.append_client_error(turn.tick, exc.error)
                _append_telemetry(
                    telemetry_path,
                    summary,
                    accepted=False,
                    error=exc.error,
                )
                print(
                    f"tick={turn.tick} rejected={exc.error} status={exc.status_code}",
                    flush=True,
                )
                continue

            completed_turns += 1
            event_logger.append_turn(turn, log_labels, mode=memory.mode)
            memory.save(memory_path)
            memory.write_stats(stats_path, turn)
            _append_telemetry(telemetry_path, summary, accepted=True)
            decision_text = " | ".join(summary.decisions[:8]) or "wait"
            print(
                f"tick={accepted.tick} accepted={accepted.accepted} "
                f"resources={summary.resources}/{summary.resource_capacity} "
                f"population={summary.population} enemies={summary.visible_enemies} "
                f"unit_actions={summary.unit_actions} core_action={summary.has_core_action} "
                f"events={summary.previous_events} decisions={decision_text}",
                flush=True,
            )
            if max_turns is not None and completed_turns >= max_turns:
                return


def main() -> int:
    parser = argparse.ArgumentParser(description="Adaptive Arena Hero tactic")
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Stop after this many accepted Turns (default: run until Ctrl-C).",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("ARENA_HERO_BASE_URL", "https://api.arenahero.io"),
    )
    parser.add_argument(
        "--websocket-url",
        default=os.environ.get("ARENA_HERO_WEBSOCKET_URL"),
    )
    parser.add_argument(
        "--memory-file",
        type=Path,
        default=Path(os.environ.get("ARENA_HERO_MEMORY_FILE", ".arena_hero_memory.json")),
    )
    parser.add_argument(
        "--telemetry-file",
        type=Path,
        default=Path(os.environ.get("ARENA_HERO_TELEMETRY_FILE", "arena_hero_telemetry.jsonl")),
    )
    parser.add_argument(
        "--stats-file",
        type=Path,
        default=Path(os.environ.get("ARENA_HERO_STATS_FILE", ".arena_hero_stats.json")),
    )
    parser.add_argument(
        "--event-log-file",
        type=Path,
        default=Path(
            os.environ.get("ARENA_HERO_EVENT_LOG_FILE", str(DEFAULT_LOG_PATH))
        ),
    )
    args = parser.parse_args()

    if args.max_turns is not None and args.max_turns < 1:
        parser.error("--max-turns must be positive")

    try:
        play(
            load_api_key(),
            base_url=args.base_url,
            websocket_url=args.websocket_url,
            max_turns=args.max_turns,
            memory_path=args.memory_file,
            telemetry_path=args.telemetry_file,
            stats_path=args.stats_file,
            event_log_path=args.event_log_file,
        )
    except KeyboardInterrupt:
        print("Stopped by user.", flush=True)
        return 0
    except (AuthenticationError, PolicyViolationError) as exc:
        print(f"Arena Hero authentication stopped: {type(exc).__name__}", file=sys.stderr)
        return 2
    except ProtocolError:
        print(
            "Arena Hero protocol mismatch. Upgrade the official arena-hero SDK and retry.",
            file=sys.stderr,
        )
        return 3
    except TransportError as exc:
        print(f"Arena Hero transport failure: {type(exc).__name__}", file=sys.stderr)
        return 4
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
