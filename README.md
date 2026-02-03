# Starlink TapHome MQTT Bridge

`starlink-taphome-bridge` is a Python CLI that polls Starlink Dish telemetry and publishes it to MQTT
for TapHome, while also listening for heater (snow-melt) commands.

## Features

- Periodic async polling of Starlink gRPC fields (field names match gRPC).
- MQTT retained telemetry topics for TapHome.
- Command subscription for heater mode with acknowledgements.
- `--once` one-shot mode or long-running daemon mode.
- Alpine-friendly (uv/uvx compatible).

## Topics

Default prefix: `taphome/starlink`

Telemetry (retained, gRPC field names):
- `{prefix}/<grpc-field>` (path format `{prefix}/field` or `{prefix}/nested/field`)
- `{prefix}/all` (JSON of published fields, optional with `--json`)

Commands:
- `{prefix}/cmd/heater_mode/set` payload: `off|on|auto` (also accepts `0/1`, `true/false`)
- `{prefix}/cmd/heater_mode/ack` JSON acknowledgement payload

Status:
- `{prefix}/status` retained `online`/`offline`

## Alpine Install

```sh
apk add --no-cache python3 py3-pip
pip install --user uv
```

Install and run (using `uvx`):

```sh
uvx starlink-taphome-bridge run --once \
  --mqtt-host 192.168.1.10 \
  --topic-prefix taphome/starlink
```

For a pinned version:

```sh
uvx --from starlink-taphome-bridge==0.1.0 starlink-taphome-bridge run --once
```

## Example TapHome MQTT Mapping

| Topic | Payload | Notes |
| --- | --- | --- |
| `taphome/starlink/pop_ping_latency_ms` | float | milliseconds |
| `taphome/starlink/device_state/uptime_s` | int | uptime seconds |
| `taphome/starlink/dish_config/snow_melt_mode` | string | `AUTO`, `ALWAYS_ON`, `ALWAYS_OFF` |
| `taphome/starlink/all` | json | published fields snapshot |
| `taphome/starlink/status` | string | `online`/`offline` |

## OpenRC Service (Alpine)

Save as `/etc/init.d/starlink-taphome-bridge`:

```sh
#!/sbin/openrc-run

command="/usr/bin/uvx"
command_args="starlink-taphome-bridge run --daemon --interval 10 --mqtt-host 192.168.1.10"
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
starlink-taphome-bridge run --help
starlink-taphome-bridge topics --topic-prefix taphome/starlink
starlink-taphome-bridge version
```

Filter published fields:

```sh
starlink-taphome-bridge run \
  --field device_state.uptime_s \
  --field dish_config.snow_melt_mode \
  --field device_info \
  --field device_state
```

## Development Notes

- The Starlink integration requires the `starlink-client` Python package (local gRPC access).
  The default dependency is `starlink-client` in `pyproject.toml`. If you use a GitHub fork
  instead, replace the dependency with a direct git URL that provides the `starlink_client`
  package.
- If you run the CLI directly from source without installing the package, use:

```sh
PYTHONPATH=src uv run python -m starlink_taphome_bridge.cli run --help
```
