# Project Guidelines

## Overview
- This project syncs AnyList and Alexa shopping lists.
- AnyList is the source of truth; Alexa may be clobbered to restore consistency.
- Runtime failures are expected to be recoverable; do not remove retry/recovery logic.

## Code Style
- Use Python and keep changes small and targeted.
- Preserve the existing logging style and public APIs unless a change is required.
- Prefer minimal edits over broad refactors.
- Avoid changing the config schema unless absolutely necessary.

## Architecture
- `server.py` orchestrates startup, login, retry, and cleanup.
- `alexa.py` handles Selenium-based Amazon automation and cookie persistence.
- `anylist.py` handles the AnyList API and websocket updates.
- `synchronizer.py` contains journal-based transaction recovery and sync logic.
- Keep the journal/recovery flow intact; it is load-bearing.

## Build and Test
- Preferred runtime is the Docker/Podman image built from the `Dockerfile`.
- Build with `podman build . -t alexa2anylist` or the equivalent Docker command.
- Run with the `config/` directory mounted as `CONFIG_PATH`, and mount `/out` for Selenium screenshots and HTML dumps.
- Use `python -m unittest discover -s tests -v` for local test runs when working on code that has stubbed dependencies.

## Conventions
- Use `CONFIG_PATH` for config files and credential caches.
- Keep Alexa session cookies and AnyList token cache behavior working across restarts.
- When debugging Selenium failures, save screenshots and DOM snapshots to `/out`.
- Preserve the existing retry loop and websocket reconnection behavior; transient failures should not terminate the whole service.
