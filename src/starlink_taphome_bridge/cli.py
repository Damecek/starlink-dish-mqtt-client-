from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import click

from starlink_taphome_bridge import __version__
from starlink_taphome_bridge.logging import configure_logging
from starlink_taphome_bridge.models import AppliedResult
from starlink_taphome_bridge.mqtt_bridge import Backoff, MqttBridge, MqttConfig, Topics
from starlink_taphome_bridge.starlink import StarlinkConfig, StarlinkGrpcClient

logger = logging.getLogger(__name__)


@dataclass
class RunConfig:
    mqtt: MqttConfig
    topics: Topics
    interval: float
    once: bool
    daemon: bool
    publish_json: bool
    publish_missing: bool
    dish: StarlinkConfig
    backoff_min: float
    backoff_max: float
    field_filters: tuple[str, ...]


def _default_client_id() -> str:
    hostname = socket.gethostname()
    seed = uuid.uuid5(uuid.NAMESPACE_DNS, f"{hostname}-{os.getpid()}")
    return f"starlink-taphome-{seed.hex[:8]}"


def _normalize_heater_mode(value: str) -> str:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "on"}:
        return "on"
    if lowered in {"0", "false", "off"}:
        return "off"
    if lowered == "auto":
        return "auto"
    raise ValueError(f"Invalid heater mode: {value}")


def _normalize_fields(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values:
        for chunk in raw.split(","):
            value = chunk.strip().replace("/", ".")
            if not value:
                continue
            normalized.append(value.strip("."))
    return tuple(normalized)


async def _run_bridge(config: RunConfig) -> None:
    stop_event = asyncio.Event()

    def _handle_stop(*_: Any) -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_stop)

    starlink = StarlinkGrpcClient(config.dish)

    async def on_command(payload: str) -> AppliedResult:
        try:
            normalized = _normalize_heater_mode(payload)
        except ValueError as exc:
            logger.warning("Invalid heater command payload: %s", payload)
            return AppliedResult(requested=payload, applied=None, success=False, message=str(exc))
        return await starlink.set_heater_mode(normalized)

    bridge = MqttBridge(
        config=config.mqtt,
        topics=config.topics,
        on_command=on_command,
        publish_json=config.publish_json,
        publish_missing=config.publish_missing,
        field_filters=config.field_filters,
    )

    mqtt_backoff = Backoff(config.backoff_min, config.backoff_max)

    async def connect_loop() -> None:
        while not stop_event.is_set():
            try:
                await bridge.connect()
                await bridge.wait_connected()
                mqtt_backoff.reset()
                stop_task = asyncio.create_task(stop_event.wait())
                disconnect_task = asyncio.create_task(bridge.wait_disconnected())
                done, _ = await asyncio.wait(
                    [stop_task, disconnect_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in (stop_task, disconnect_task):
                    if task not in done:
                        task.cancel()
                if stop_task in done:
                    break
            except Exception as exc:  # noqa: BLE001
                logger.warning("MQTT connection error: %s", exc)
                delay = mqtt_backoff.next_delay()
                await asyncio.sleep(delay)

    async def poll_loop() -> None:
        poll_backoff = Backoff(config.backoff_min, config.backoff_max)
        while not stop_event.is_set():
            try:
                telemetry = await starlink.get_telemetry()
                await bridge.publish_telemetry(telemetry)
                poll_backoff.reset()
                timeout = 0 if config.once else config.interval
            except Exception as exc:  # noqa: BLE001
                logger.warning("Telemetry poll failed: %s", exc)
                timeout = poll_backoff.next_delay()
            if config.once:
                stop_event.set()
                break
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                continue

    await asyncio.gather(connect_loop(), poll_loop())
    await bridge.publish_status("offline")
    await bridge.disconnect()


@click.group()
@click.version_option(__version__, prog_name="starlink-taphome-bridge")
def main() -> None:
    """Starlink TapHome MQTT bridge."""


@main.command()
@click.option("--mqtt-host", default="localhost", show_default=True)
@click.option("--mqtt-port", default=1883, show_default=True, type=int)
@click.option("--mqtt-username")
@click.option("--mqtt-password")
@click.option("--mqtt-client-id", default=None)
@click.option("--mqtt-qos", default=1, show_default=True, type=click.IntRange(0, 2))
@click.option("--mqtt-keepalive", default=60, show_default=True, type=int)
@click.option("--mqtt-tls/--no-mqtt-tls", default=False, show_default=True)
@click.option("--mqtt-ca-file", type=click.Path())
@click.option("--mqtt-cert-file", type=click.Path())
@click.option("--mqtt-key-file", type=click.Path())
@click.option("--topic-prefix", default="taphome/starlink", show_default=True)
@click.option("--interval", default=10.0, show_default=True, type=float)
@click.option("--once", is_flag=True, default=False, show_default=True)
@click.option("--daemon/--no-daemon", default=True, show_default=True)
@click.option(
    "--log-level",
    default="INFO",
    show_default=True,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
)
@click.option("--json", "publish_json", is_flag=True, default=False, show_default=True)
@click.option("--retain/--no-retain", default=True, show_default=True)
@click.option("--publish-missing", is_flag=True, default=False, show_default=True)
@click.option(
    "--field",
    "fields",
    multiple=True,
    help="Publish only selected gRPC fields (repeatable). Example: --field device_state.uptime_s",
)
@click.option("--dish-host", default="192.168.100.1", show_default=True)
@click.option("--dish-port", default=9200, show_default=True, type=int)
@click.option("--backoff-min", default=1.0, show_default=True, type=float)
@click.option("--backoff-max", default=60.0, show_default=True, type=float)
def run(**kwargs: Any) -> None:
    """Run the bridge."""
    configure_logging(kwargs["log_level"])
    client_id = kwargs["mqtt_client_id"] or _default_client_id()
    once = kwargs["once"]
    daemon = kwargs["daemon"]
    if once:
        daemon = False
    config = RunConfig(
        mqtt=MqttConfig(
            host=kwargs["mqtt_host"],
            port=kwargs["mqtt_port"],
            username=kwargs["mqtt_username"],
            password=kwargs["mqtt_password"],
            client_id=client_id,
            qos=kwargs["mqtt_qos"],
            keepalive=kwargs["mqtt_keepalive"],
            tls=kwargs["mqtt_tls"],
            ca_file=kwargs["mqtt_ca_file"],
            cert_file=kwargs["mqtt_cert_file"],
            key_file=kwargs["mqtt_key_file"],
            retain=kwargs["retain"],
        ),
        topics=Topics(prefix=kwargs["topic_prefix"]),
        interval=kwargs["interval"],
        once=once,
        daemon=daemon,
        publish_json=kwargs["publish_json"],
        publish_missing=kwargs["publish_missing"],
        dish=StarlinkConfig(host=kwargs["dish_host"], port=kwargs["dish_port"]),
        backoff_min=kwargs["backoff_min"],
        backoff_max=kwargs["backoff_max"],
        field_filters=_normalize_fields(kwargs["fields"]),
    )
    if not daemon:
        asyncio.run(_run_bridge(config))
        return
    try:
        asyncio.run(_run_bridge(config))
    except KeyboardInterrupt:
        logger.info("Bridge stopped")


@main.command()
@click.option("--topic-prefix", default="taphome/starlink", show_default=True)
def topics(topic_prefix: str) -> None:
    """Print all MQTT topics used."""
    topics = Topics(prefix=topic_prefix)
    for topic in (
        topics.status,
        f"{topic_prefix}/<grpc-field>",
        topics.all_fields,
        topics.cmd_heater_set,
        topics.cmd_heater_ack,
    ):
        click.echo(topic)


@main.command()
def version() -> None:
    """Print version."""
    click.echo(json.dumps({"version": __version__}))


if __name__ == "__main__":
    main()
