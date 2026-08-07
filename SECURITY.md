# Security Policy

## Credentials

Never commit or publish any of the following:

- `.env`
- `.arena_hero_api_key.dpapi`
- API keys in shell history, screenshots, issue bodies, or CI logs
- Runtime telemetry or state snapshots that you do not intend to disclose

On Windows, `set_key.ps1` stores the Arena Hero API Key with DPAPI for the current user. The encrypted file is machine/user-bound and is ignored by Git. On other platforms, inject `ARENA_HERO_API_KEY` through the platform's secret store or a local ignored `.env` file.

The Agent, event logger, and overlay server intentionally omit values whose keys contain `api`, `authorization`, `credential`, `secret`, or `token`.

## Local overlay boundary

The overlay server binds only to `127.0.0.1`. Write endpoints accept extension origins and reject normal web origins. Do not modify it to listen on a public interface without adding authentication and a threat model.

## Reporting a vulnerability

Use GitHub private vulnerability reporting for the repository when available. Do not open a public issue containing a credential, exploit payload, or private Arena Hero state. Revoke an exposed API Key before sending any report.

## Before publishing a fork

Run the release checks in [docs/RELEASING.md](docs/RELEASING.md), inspect `git status --ignored`, and search the complete Git history if the fork ever tracked local credential files.
