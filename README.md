# Starlink TapHome MQTT Bridge

`starlink-taphome-bridge` is a Python CLI that polls Starlink Dish telemetry and publishes it to MQTT
for TapHome, while also listening for MQTT `/set` commands and applying writable fields to Starlink.

## Features

- Periodic async polling of Starlink gRPC fields (field names match gRPC).
- MQTT retained telemetry topics for TapHome.
- Generic `/set` command subscription derived from MQTT topic path.
- `--once` one-shot mode or long-running daemon mode.
- Alpine-friendly (uv compatible).

## Topics

Default prefix: `taphome/starlink`

Telemetry (retained, gRPC field names):
- `{prefix}/<grpc-field>` (path format `{prefix}/field` or `{prefix}/nested/field`)
- `{prefix}/all` (JSON of published fields, optional with `--json`)

Commands:
- `{prefix}/<grpc-field>/set` payload: value to write via gRPC (example: `AUTO`)
- `{prefix}/<grpc-field>/ack` JSON acknowledgement payload

Example:
- `starlink/dish_config/snow_melt_mode/set` payload `AUTO`
- Some write operations can return `PERMISSION_DENIED` depending on dish firmware/account permissions.

Status:
- `{prefix}/status` retained `online`/`offline`

## Alpine Install

```sh
apk add --no-cache python3 py3-pip
pip install --user uv
```

Run from this repo with `uv`:

```sh
uv run starlink-taphome-bridge run --once \
  --mqtt-host 192.168.68.200 --mqtt-port 1883 \
  --mqtt-username taphome --mqtt-password pass \
  --topic-prefix starlink \
  --dish-host 192.168.100.1 --dish-port 9200 \
  --all-fields \
  --log-level DEBUG
```

Run `topics` and `version` commands:

```sh
uv run starlink-taphome-bridge topics --topic-prefix starlink
uv run starlink-taphome-bridge version
```

## Example TapHome MQTT Mapping

| Topic | Payload | Notes |
| --- | --- | --- |
| `starlink/pop_ping_latency_ms` | float | milliseconds |
| `starlink/device_state/uptime_s` | int | uptime seconds |
| `starlink/dish_config/snow_melt_mode` | string | `AUTO`, `ALWAYS_ON`, `ALWAYS_OFF` |
| `starlink/all` | json | published fields snapshot |
| `starlink/status` | string | `online`/`offline` |

## OpenRC Service (Alpine)

Save as `/etc/init.d/starlink-taphome-bridge`:

```sh
#!/sbin/openrc-run

command="/usr/bin/uv"
command_args="run starlink-taphome-bridge run --daemon --interval 10 --mqtt-host 192.168.68.200 --mqtt-port 1883 --mqtt-username taphome --mqtt-password pass --topic-prefix starlink --dish-host 192.168.100.1 --dish-port 9200 --all-fields --log-level DEBUG"
command_background="yes"
pidfile="/run/starlink-taphome-bridge.pid"

start_pre() {
  checkpath --file --mode 0644 "$pidfile"
}

respawn_delay=5
respawn_max=0
```

Then:

```sh
chmod +x /etc/init.d/starlink-taphome-bridge
rc-update add starlink-taphome-bridge default
rc-service starlink-taphome-bridge start
```

## Usage

```sh
uv run starlink-taphome-bridge run --help
uv run starlink-taphome-bridge topics --topic-prefix starlink
uv run starlink-taphome-bridge version
```

Filter published fields:

```sh
uv run starlink-taphome-bridge run --once \
  --mqtt-host 192.168.68.200 --mqtt-port 1883 \
  --mqtt-username taphome --mqtt-password pass \
  --topic-prefix starlink \
  --dish-host 192.168.100.1 --dish-port 9200 \
  --log-level DEBUG \
  --field device_state.uptime_s \
  --field dish_config.snow_melt_mode \
  --field device_info \
  --field device_state
```

Publish all available gRPC fields explicitly:

```sh
uv run starlink-taphome-bridge run --once \
  --mqtt-host 192.168.68.200 --mqtt-port 1883 \
  --mqtt-username taphome --mqtt-password pass \
  --topic-prefix starlink \
  --dish-host 192.168.100.1 --dish-port 9200 \
  --all-fields \
  --log-level DEBUG
```

Notes:
- Without any `--field`, all available fields are already published.
- `--all-fields` is useful when you want to override a templated command that normally includes `--field`.

## Development Notes

- If you want to run the package entrypoint from this repo, install it into the local venv first:

```sh
uv sync
uv run starlink-taphome-bridge run --help
```

- For a one-off run without syncing, you can install the project editably just for the command:

```sh
uv run --with-editable . starlink-taphome-bridge run --help
```

- The Starlink integration requires the `starlink-client` Python package (local gRPC access).
  The default dependency is `starlink-client` in `pyproject.toml`. If you use a GitHub fork
  instead, replace the dependency with a direct git URL that provides the `starlink_client`
  package.
- If you prefer module execution instead of the package entrypoint, use:

```sh
uv run starlink-taphome-bridge run --help
```
