from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from getpass import getpass
from pathlib import Path

from arena_hero import (
    APIError,
    ArenaHeroClient,
    AuthenticationError,
    PolicyViolationError,
    ProtocolError,
    TransportError,
    Turn,
)

import arena_hero_strategy as strategy_module


DecisionSummary = strategy_module.DecisionSummary
TacticMemory = strategy_module.TacticMemory


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
) -> None:
    completed_turns = 0
    strategy = strategy_module
    strategy_file = Path(strategy.__file__ or "arena_hero_strategy.py")
    strategy_mtime = strategy_file.stat().st_mtime_ns
    memory = strategy.TacticMemory.load(memory_path)
    tactic = strategy.SmartTactic(memory)

    with ArenaHeroClient(
        api_key=api_key,
        base_url=base_url,
        websocket_url=websocket_url,
    ) as game:
        for turn in game.turns():
            current_mtime = strategy_file.stat().st_mtime_ns
            if current_mtime != strategy_mtime:
                memory.save(memory_path)
                strategy = importlib.reload(strategy)
                strategy_file = Path(strategy.__file__ or strategy_file)
                strategy_mtime = strategy_file.stat().st_mtime_ns
                memory = strategy.TacticMemory.load(memory_path)
                tactic = strategy.SmartTactic(memory)
                print(f"tick={turn.tick} strategy_reloaded=True", flush=True)
            summary = tactic.choose_actions(turn)
            try:
                accepted = turn.submit()
            except APIError as exc:
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
