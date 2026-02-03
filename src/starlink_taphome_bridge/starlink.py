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
            from google.protobuf.descriptor import FieldDescriptor
            from spacex.api.device import device_pb2
            from spacex.api.device import dish_config_pb2
            from spacex.api.device import dish_pb2
            from starlink_client.grpc_client import GrpcClient
        except ImportError as exc:
            raise StarlinkClientError(
                "starlink-client package is required for Starlink integration"
            ) from exc
        self._device_pb2 = device_pb2
        self._dish_config_pb2 = dish_config_pb2
        self._dish_pb2 = dish_pb2
        self._field_descriptor = FieldDescriptor
        self._grpc_client = GrpcClient(f"{self._config.host}:{self._config.port}")

    async def get_telemetry(self) -> Telemetry:
        return await asyncio.to_thread(self._get_telemetry_sync)

    def _get_telemetry_sync(self) -> Telemetry:
        status = self._get_status()
        config = self._get_config()
        fields: dict[str, Any] = {}
        if status is not None:
            fields.update(self._proto_to_dict(status))
        if config is not None:
            fields.update(self._proto_to_dict(config))
        return Telemetry(fields=fields)

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
        request = self._device_pb2.Request(get_status=self._device_pb2.GetStatusRequest())
        response = self._grpc_client.call(request)
        if hasattr(response, "dish_get_status") and response.dish_get_status is not None:
            return response.dish_get_status
        return response

    def _get_config(self) -> Any | None:
        request = self._device_pb2.Request(dish_get_config=self._dish_pb2.DishGetConfigRequest())
        response = self._grpc_client.call(request)
        if hasattr(response, "dish_get_config") and response.dish_get_config is not None:
            return response.dish_get_config
        return None

    def _apply_heater_mode(self, mode: str) -> str:
        normalized = self._normalize_heater_mode(mode)
        snow_melt_mode = self._heater_mode_to_enum(normalized)
        dish_config = self._dish_config_pb2.DishConfig(
            snow_melt_mode=snow_melt_mode,
            apply_snow_melt_mode=True,
        )
        request = self._device_pb2.Request(
            dish_set_config=self._dish_pb2.DishSetConfigRequest(dish_config=dish_config)
        )
        self._grpc_client.call(request)
        return normalized

    def _proto_to_dict(self, message: Any) -> dict[str, Any]:
        if message is None or not hasattr(message, "DESCRIPTOR"):
            return {}
        result: dict[str, Any] = {}
        for field in message.DESCRIPTOR.fields:
            value = getattr(message, field.name)
            if field.label == self._field_descriptor.LABEL_REPEATED:
                if field.cpp_type == self._field_descriptor.CPPTYPE_MESSAGE:
                    result[field.name] = [self._proto_to_dict(item) for item in value]
                else:
                    result[field.name] = list(value)
                continue
            if field.cpp_type == self._field_descriptor.CPPTYPE_MESSAGE:
                if field.has_presence and not message.HasField(field.name):
                    result[field.name] = {}
                else:
                    result[field.name] = self._proto_to_dict(value)
                continue
            if field.cpp_type == self._field_descriptor.CPPTYPE_ENUM:
                enum_value = field.enum_type.values_by_number.get(value)
                result[field.name] = enum_value.name if enum_value is not None else value
                continue
            result[field.name] = value
        return result

    def _normalize_heater_mode(self, value: Any) -> str:
        if isinstance(value, int):
            try:
                name = self._dish_config_pb2.DishConfig.SnowMeltMode.Name(value)
                return self._normalize_heater_mode(name)
            except Exception:  # noqa: BLE001
                return str(value).lower()
        text = str(value).strip().lower()
        mapping = {
            "auto": "auto",
            "always_on": "on",
            "always on": "on",
            "always-on": "on",
            "always-off": "off",
            "always_off": "off",
            "always off": "off",
            "alwayson": "on",
            "alwaysoff": "off",
            "on": "on",
            "off": "off",
        }
        if text in mapping:
            return mapping[text]
        if text.startswith("snowmeltmode."):
            return self._normalize_heater_mode(text.split(".", 1)[1])
        return text

    def _heater_mode_to_enum(self, mode: str) -> int:
        normalized = self._normalize_heater_mode(mode)
        mapping = {
            "auto": "AUTO",
            "on": "ALWAYS_ON",
            "off": "ALWAYS_OFF",
        }
        if normalized not in mapping:
            raise StarlinkClientError(f"Unsupported heater mode: {mode}")
        return self._dish_config_pb2.DishConfig.SnowMeltMode.Value(mapping[normalized])
