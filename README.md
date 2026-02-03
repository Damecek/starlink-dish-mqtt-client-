# Starlink TapHome MQTT Bridge

`starlink-taphome-bridge` is a Python CLI that polls Starlink Dish telemetry and publishes it to MQTT
for TapHome, while also listening for heater (snow-melt) commands.

## Features

- Periodic async telemetry polling (power, latency, heater mode, uptime).
- MQTT retained telemetry topics for TapHome.
- Command subscription for heater mode with acknowledgements.
- `--once` one-shot mode or long-running daemon mode.
- Alpine-friendly (uv/uvx compatible).

## Topics

Default prefix: `taphome/starlink`

Telemetry (retained):
- `{prefix}/telemetry/power_w` (float)
- `{prefix}/telemetry/latency_ms` (float)
- `{prefix}/telemetry/heater_mode` (string)
- `{prefix}/telemetry/uptime_s` (int)
- `{prefix}/telemetry/last_update_ts` (int)
- `{prefix}/telemetry/all` (JSON, optional with `--json`)

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
| `taphome/starlink/telemetry/power_w` | float | watts |
| `taphome/starlink/telemetry/latency_ms` | float | milliseconds |
| `taphome/starlink/telemetry/heater_mode` | string | `off`, `on`, or `auto` |
| `taphome/starlink/telemetry/uptime_s` | int | uptime seconds |
| `taphome/starlink/telemetry/last_update_ts` | int | unix epoch |
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

## Development Notes

- The Starlink integration requires a `starlink_grpc` Python module. The default dependency is
  `starlink-grpc-core` in `pyproject.toml`. If you use a GitHub fork instead, replace the
  dependency with a direct git URL that provides the `starlink_grpc` module.
- If you run the CLI directly from source without installing the package, use:

```sh
PYTHONPATH=src uv run python -m starlink_taphome_bridge.cli run --help
```
