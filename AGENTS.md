# Repository Guidelines
This is a living document. Update it as the project evolves and new workflows, constraints, or conventions emerge.

## Project Structure & Module Organization
- Source code lives in `src/starlink_taphome_bridge/`.
- The CLI entrypoint is `src/starlink_taphome_bridge/cli.py` and maps to the `starlink-taphome-bridge` command.
- Core logic is split into focused modules: `mqtt_bridge.py` (MQTT publishing/subscriptions), `starlink.py` (Dish telemetry), and `models.py` (data shapes).
- Example service setup files live under `examples/` (e.g., `examples/openrc/`).

## Build, Test, and Development Commands
- Run the CLI locally (installed):
  - `starlink-taphome-bridge run --help` to view options.
- Run without installing (module entrypoint):
  - `PYTHONPATH=src python -m starlink_taphome_bridge.cli run --once --mqtt-host 192.168.1.10`.
- Alpine/uvx usage (from README):
  - `uvx starlink-taphome-bridge run --once --mqtt-host 192.168.1.10`.
- If you use `uv` for local development, the module entrypoint works without installation:
  - `PYTHONPATH=src uv run python -m starlink_taphome_bridge.cli run --help`.
- There is no build step beyond standard Python packaging (see `pyproject.toml`).

## Coding Style & Naming Conventions
- Python 3.11+ only.
- Formatting and linting are handled by Ruff:
  - Line length: 100, double quotes, space indents.
  - Imports are sorted with Ruff/isort using `starlink_taphome_bridge` as first-party.
- Naming: modules and packages use `snake_case`; classes use `PascalCase`.

## Testing Guidelines
- No automated test framework is configured yet (no `tests/` directory in this repo).
- If you add tests, prefer `pytest` and place them under `tests/` with names like `test_*.py`.

## Commit & Pull Request Guidelines
- Commit history is minimal and does not follow a strict convention. Keep messages short and descriptive (e.g., “Add MQTT retry backoff”).
- PRs should include:
  - A concise summary of behavior changes.
  - Any new/updated CLI flags.
  - Example command(s) to validate the change.

## Configuration & Safety Notes
- Configuration is CLI-driven (see `--mqtt-host`, `--topic-prefix`, `--interval`, and `--field`). Avoid hardcoding secrets.
- MQTT topics and retained message behavior are documented in `README.md` and follow gRPC field names.
- Starlink integration depends on the `starlink_client` package; ensure the dependency provides that import.
