from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

from gmqtt import Client as MqttClient
from gmqtt import Message as MqttMessage

from starlink_taphome_bridge.models import AppliedResult, Telemetry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Topics:
    prefix: str

    @property
    def status(self) -> str:
        return f"{self.prefix}/status"

    @property
    def all_fields(self) -> str:
        return f"{self.prefix}/all"

    def field(self, path: str) -> str:
        return f"{self.prefix}/{path}"

    @property
    def cmd_heater_set(self) -> str:
        return f"{self.prefix}/cmd/heater_mode/set"

    @property
    def cmd_heater_ack(self) -> str:
        return f"{self.prefix}/cmd/heater_mode/ack"


@dataclass
class MqttConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    client_id: str
    qos: int
    keepalive: int
    tls: bool
    ca_file: str | None
    cert_file: str | None
    key_file: str | None
    retain: bool


class Backoff:
    def __init__(self, minimum: float, maximum: float) -> None:
        self._minimum = minimum
        self._maximum = maximum
        self._current = minimum

    def reset(self) -> None:
        self._current = self._minimum

    def next_delay(self) -> float:
        delay = self._current
        self._current = min(self._current * 2, self._maximum)
        return delay


class MqttBridge:
    def __init__(
        self,
        config: MqttConfig,
        topics: Topics,
        on_command: Callable[[str], Awaitable[AppliedResult]],
        publish_json: bool,
        publish_missing: bool,
        field_filters: Iterable[str],
    ) -> None:
        self._config = config
        self._topics = topics
        self._on_command = on_command
        self._publish_json = publish_json
        self._publish_missing = publish_missing
        self._field_filters = tuple(self._normalize_filter(value) for value in field_filters if value)
        self._warned_filters: set[str] = set()
        will_message = MqttMessage(self._topics.status, "offline", qos=config.qos, retain=True)
        self._client = MqttClient(config.client_id, will_message=will_message)
        self._connected = asyncio.Event()
        self._disconnected = asyncio.Event()
        self._last_payloads: dict[str, tuple[str, int | None, bool]] = {}
        self._setup_client()

    def _setup_client(self) -> None:
        if self._config.username:
            self._client.set_auth_credentials(self._config.username, self._config.password or "")
        if self._config.tls:
            self._client.set_tls(
                ca_certs=self._config.ca_file,
                certfile=self._config.cert_file,
                keyfile=self._config.key_file,
            )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    async def connect(self) -> None:
        await self._client.connect(self._config.host, self._config.port, keepalive=self._config.keepalive)

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def wait_connected(self) -> None:
        await self._connected.wait()

    async def wait_disconnected(self) -> None:
        await self._disconnected.wait()

    async def publish_status(self, status: str) -> None:
        await self.publish(self._topics.status, status, retain=True)

    async def publish(self, topic: str, payload: str, retain: bool | None = None) -> None:
        retain_flag = self._config.retain if retain is None else retain
        self._last_payloads[topic] = (payload, self._config.qos, retain_flag)
        if not self._client.is_connected:
            return
        self._client.publish(topic, payload, qos=self._config.qos, retain=retain_flag)

    async def publish_telemetry(self, telemetry: Telemetry) -> None:
        now_ts = int(time.time())
        flat_fields = self._flatten_fields(telemetry.to_dict())
        self._warn_unknown_filters(flat_fields)
        filtered_fields = {
            key: value for key, value in flat_fields.items() if self._should_publish(key)
        }
        for path, value in filtered_fields.items():
            topic = self._topics.field(path.replace(".", "/"))
            self._publish_if_value(topic, value)

        if self._publish_json:
            payload = json.dumps(
                {"fields": filtered_fields, "timestamp": now_ts},
                separators=(",", ":"),
            )
            await self.publish(self._topics.all_fields, payload)

    async def publish_ack(self, result: AppliedResult) -> None:
        payload = json.dumps(
            {
                "requested": result.requested,
                "applied": result.applied,
                "success": result.success,
                "message": result.message,
                "timestamp": int(time.time()),
            },
            separators=(",", ":"),
        )
        await self.publish(self._topics.cmd_heater_ack, payload)

    def _publish_if_value(self, topic: str, value: Any) -> None:
        if value is None and not self._publish_missing:
            return
        if isinstance(value, (dict, list)):
            payload = json.dumps(value, separators=(",", ":"))
        else:
            payload = "" if value is None else str(value)
        if self._client.is_connected:
            self._client.publish(topic, payload, qos=self._config.qos, retain=self._config.retain)
        self._last_payloads[topic] = (payload, self._config.qos, self._config.retain)

    def _flatten_fields(self, data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                nested = self._flatten_fields(value, path)
                if nested:
                    result.update(nested)
                else:
                    result[path] = {}
                continue
            result[path] = value
        return result

    def _normalize_filter(self, value: str) -> str:
        normalized = value.strip().replace("/", ".")
        return normalized.strip(".")

    def _should_publish(self, path: str) -> bool:
        if not self._field_filters:
            return True
        return any(path == filt or path.startswith(f"{filt}.") for filt in self._field_filters)

    def _warn_unknown_filters(self, flat_fields: dict[str, Any]) -> None:
        if not self._field_filters:
            return
        for filt in self._field_filters:
            if filt in self._warned_filters:
                continue
            if any(path == filt or path.startswith(f"{filt}.") for path in flat_fields):
                continue
            logger.warning("Requested gRPC field filter not found: %s", filt)
            self._warned_filters.add(filt)

    def _on_connect(self, client: MqttClient, flags: dict[str, Any], rc: int, properties: Any) -> None:
        logger.info("Connected to MQTT broker")
        self._connected.set()
        self._disconnected.clear()
        client.subscribe(self._topics.cmd_heater_set, qos=self._config.qos)
        asyncio.create_task(self.publish_status("online"))
        self._republish_cached()

    def _republish_cached(self) -> None:
        for topic, (payload, qos, retain) in self._last_payloads.items():
            self._client.publish(topic, payload, qos=qos or 0, retain=retain)

    def _on_disconnect(self, client: MqttClient, packet: Any, exc: Exception | None = None) -> None:
        logger.warning("Disconnected from MQTT broker", exc_info=exc)
        self._connected.clear()
        self._disconnected.set()

    def _on_message(self, client: MqttClient, topic: str, payload: bytes, qos: int, properties: Any) -> None:
        text = payload.decode("utf-8").strip()
        if topic == self._topics.cmd_heater_set:
            logger.info("Received heater command: %s", text)
            asyncio.create_task(self._handle_command(text))

    async def _handle_command(self, payload: str) -> None:
        result = await self._on_command(payload)
        await self.publish_ack(result)

    def iter_topics(self) -> Iterable[str]:
        return (
            self._topics.status,
            self._topics.all_fields,
            self._topics.cmd_heater_set,
            self._topics.cmd_heater_ack,
        )
