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
- `alexa_api.py` handles the Alexa shopping list API: login as a virtual Alexa device, refresh-token/cookie persistence, and the `AlexaShoppingList` adapter the synchronizer uses.
- `anylist.py` handles the AnyList API and websocket updates.
- `synchronizer.py` contains journal-based transaction recovery and sync logic.
- Keep the journal/recovery flow intact; it is load-bearing.

## Build and Test
- Preferred runtime is the Docker/Podman image built from the `Dockerfile`.
- Build with `podman build . -t alexa2anylist` or the equivalent Docker command.
- Run with the `config/` directory mounted as `CONFIG_PATH`.
- Use `python -m unittest discover -s tests -v` for local test runs when working on code that has stubbed dependencies.

## Conventions
- Use `CONFIG_PATH` for config files and credential caches.
- Keep the Alexa credential cache (`alexa-credentials.json`) and AnyList token cache working across restarts; password logins to either service are throttled to avoid getting blocked.
- Preserve the existing retry loop and websocket reconnection behavior; transient failures should not terminate the whole service.
