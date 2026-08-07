# Contributing

## Compatibility

Tactical changes must remain compatible with Arena Hero gameplay rules v0.14 and the official Python SDK `>=0.2.9,<0.3`. Do not duplicate SDK transport, retry, state-model, or dynamic pricing logic.

Every Turn is an authoritative replacement. Remembered resources and enemies must retain explicit expiry and current-visibility invalidation rules.

## Development setup

```powershell
.\setup.ps1
```

Run the checks before opening a pull request:

```powershell
.\.venv\Scripts\python.exe -m compileall -q arena_hero_tactic.py arena_hero_strategy.py arena_hero_event_log.py arena_hero_route_overlay_server.py
.\.venv\Scripts\python.exe -m unittest
node arena_hero_route_overlay\test_overlay_core.js
.\.venv\Scripts\python.exe -m pip check
```

## Change expectations

- Add focused tests for new economy, combat, movement, production, control, or persistence behavior.
- Keep API keys and runtime files out of fixtures and logs.
- Preserve the foreground and background launch paths.
- Update `docs/STRATEGY.md` when a constant or decision priority changes materially.
- Update `docs/USAGE.md` when a CLI argument, environment variable, control field, or overlay workflow changes.
- Treat a failed or missed Tick honestly; do not hide submission errors in logging.
