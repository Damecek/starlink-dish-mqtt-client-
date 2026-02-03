from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from starlink_taphome_bridge.models import AppliedResult, Telemetry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StarlinkConfig:
    host: str
    port: int


class StarlinkClientError(RuntimeError):
    pass


class StarlinkClient:
    def __init__(self, config: StarlinkConfig) -> None:
        self._config = config

    async def get_telemetry(self) -> Telemetry:
        raise NotImplementedError

    async def set_heater_mode(self, mode: str) -> AppliedResult:
        raise NotImplementedError


class StarlinkGrpcClient(StarlinkClient):
    def __init__(self, config: StarlinkConfig) -> None:
        super().__init__(config)
        try:
            import starlink_grpc  # type: ignore
        except ImportError as exc:
            raise StarlinkClientError(
                "starlink-grpc package is required for Starlink integration"
            ) from exc
        # Support both module-style and package-style layouts.
        self._starlink_grpc = getattr(starlink_grpc, "starlink_grpc", starlink_grpc)

    async def get_telemetry(self) -> Telemetry:
        return await asyncio.to_thread(self._get_telemetry_sync)

    def _get_telemetry_sync(self) -> Telemetry:
        status = self._get_status()
        power_w = self._extract_power(status)
        latency_ms = self._extract_latency(status)
        heater_mode = self._extract_heater_mode(status)
        uptime_s = self._extract_uptime(status)
        return Telemetry(
            power_w=power_w,
            latency_ms=latency_ms,
            heater_mode=heater_mode,
            uptime_s=uptime_s,
        )

    async def set_heater_mode(self, mode: str) -> AppliedResult:
        return await asyncio.to_thread(self._set_heater_mode_sync, mode)

    def _set_heater_mode_sync(self, mode: str) -> AppliedResult:
        try:
            applied = self._apply_heater_mode(mode)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to apply heater mode")
            return AppliedResult(requested=mode, applied=None, success=False, message=str(exc))
        return AppliedResult(requested=mode, applied=applied, success=True)

    def _get_status(self) -> Any:
        client = self._maybe_client()
        if client is not None:
            return client.get_status()
        if hasattr(self._starlink_grpc, "get_status"):
            try:
                return self._starlink_grpc.get_status(host=self._config.host, port=self._config.port)
            except TypeError:
                return self._starlink_grpc.get_status()
        raise StarlinkClientError("Unable to locate starlink_grpc.get_status implementation")

    def _maybe_client(self) -> Any | None:
        client_cls = getattr(self._starlink_grpc, "GrpcClient", None)
        if client_cls is None:
            return None
        return client_cls(self._config.host, self._config.port)

    def _apply_heater_mode(self, mode: str) -> str:
        client = self._maybe_client()
        if client is not None:
            if hasattr(client, "set_heater_mode"):
                client.set_heater_mode(mode)
                return mode
            if hasattr(client, "set_dish_heater"):
                client.set_dish_heater(mode)
                return mode
        if hasattr(self._starlink_grpc, "set_heater_mode"):
            try:
                self._starlink_grpc.set_heater_mode(mode, host=self._config.host, port=self._config.port)
            except TypeError:
                self._starlink_grpc.set_heater_mode(mode)
            return mode
        if hasattr(self._starlink_grpc, "set_dish_heater"):
            try:
                self._starlink_grpc.set_dish_heater(mode, host=self._config.host, port=self._config.port)
            except TypeError:
                self._starlink_grpc.set_dish_heater(mode)
            return mode
        raise StarlinkClientError("Unable to locate starlink_grpc heater control API")

    @staticmethod
    def _extract_power(status: Any) -> float | None:
        if status is None:
            return None
        for key in ("power_w", "power_watts", "power_watts_avg"):
            value = getattr(status, key, None)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def _extract_latency(status: Any) -> float | None:
        if status is None:
            return None
        for key in ("latency_ms", "ping_latency_ms", "pop_ping_latency_ms"):
            value = getattr(status, key, None)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def _extract_heater_mode(status: Any) -> str | None:
        if status is None:
            return None
        for key in ("heater_mode", "snow_melt_mode", "dish_heater_mode"):
            value = getattr(status, key, None)
            if value is not None:
                return str(value).lower()
        return None

    @staticmethod
    def _extract_uptime(status: Any) -> int | None:
        if status is None:
            return None
        for key in ("uptime_s", "uptime_sec", "uptime"):
            value = getattr(status, key, None)
            if value is not None:
                return int(value)
        return None
