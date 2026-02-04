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

    async def set_field(self, path: str, value: str) -> AppliedResult:
        raise NotImplementedError

    async def set_heater_mode(self, mode: str) -> AppliedResult:
        return await self.set_field("dish_config.snow_melt_mode", mode)


class StarlinkGrpcClient(StarlinkClient):
    def __init__(self, config: StarlinkConfig) -> None:
        super().__init__(config)
        try:
            from google.protobuf.descriptor import FieldDescriptor
            from spacex.api.device import device_pb2, dish_config_pb2, dish_pb2
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

    async def set_field(self, path: str, value: str) -> AppliedResult:
        return await asyncio.to_thread(self._set_field_sync, path, value)

    def _set_field_sync(self, path: str, value: str) -> AppliedResult:
        try:
            applied = self._apply_field(path, value)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to apply %s=%s", path, value, exc_info=exc)
            return AppliedResult(
                requested=value,
                applied=None,
                success=False,
                message=self._format_error(exc),
            )
        return AppliedResult(requested=value, applied=applied, success=True)

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

    def _apply_field(self, path: str, value: str) -> str:
        normalized_path = path.strip().replace("/", ".").strip(".")
        if not normalized_path:
            raise StarlinkClientError("Field path is empty")

        parts = normalized_path.split(".")
        if parts[0] == "dish_config":
            parts = parts[1:]
        if not parts:
            raise StarlinkClientError("Field path does not target a writable DishConfig field")

        dish_config = self._dish_config_pb2.DishConfig()
        applied = self._set_field_on_message(dish_config, parts, value)

        apply_flag = f"apply_{parts[0]}"
        if hasattr(dish_config, apply_flag):
            setattr(dish_config, apply_flag, True)

        request = self._device_pb2.Request(
            dish_set_config=self._dish_pb2.DishSetConfigRequest(dish_config=dish_config)
        )
        self._grpc_client.call(request)
        return self._serialize_applied_value(applied)

    def _set_field_on_message(self, message: Any, parts: list[str], value: str) -> Any:
        current = message
        for part in parts[:-1]:
            field = current.DESCRIPTOR.fields_by_name.get(part)
            if field is None:
                raise StarlinkClientError(f"Unknown field: {part}")
            if field.label == self._field_descriptor.LABEL_REPEATED:
                raise StarlinkClientError(f"Repeated field paths are not supported: {part}")
            if field.cpp_type != self._field_descriptor.CPPTYPE_MESSAGE:
                raise StarlinkClientError(f"Field is not a message: {part}")
            current = getattr(current, part)

        leaf_name = parts[-1]
        leaf = current.DESCRIPTOR.fields_by_name.get(leaf_name)
        if leaf is None:
            raise StarlinkClientError(f"Unknown field: {leaf_name}")
        if leaf.label == self._field_descriptor.LABEL_REPEATED:
            raise StarlinkClientError(f"Repeated fields are not supported: {leaf_name}")

        converted = self._convert_payload(leaf, value)
        setattr(current, leaf_name, converted)
        return converted

    def _convert_payload(self, field: Any, value: str) -> Any:
        text = value.strip()
        if field.cpp_type == self._field_descriptor.CPPTYPE_ENUM:
            return self._parse_enum(field, text)
        if field.type == self._field_descriptor.TYPE_BOOL:
            lowered = text.lower()
            if lowered in {"1", "true", "on", "yes"}:
                return True
            if lowered in {"0", "false", "off", "no"}:
                return False
            raise StarlinkClientError(f"Invalid boolean value for {field.name}: {value}")
        if field.type in {
            self._field_descriptor.TYPE_INT32,
            self._field_descriptor.TYPE_INT64,
            self._field_descriptor.TYPE_SINT32,
            self._field_descriptor.TYPE_SINT64,
            self._field_descriptor.TYPE_SFIXED32,
            self._field_descriptor.TYPE_SFIXED64,
            self._field_descriptor.TYPE_UINT32,
            self._field_descriptor.TYPE_UINT64,
            self._field_descriptor.TYPE_FIXED32,
            self._field_descriptor.TYPE_FIXED64,
        }:
            try:
                return int(text, 10)
            except ValueError as exc:
                raise StarlinkClientError(
                    f"Invalid integer value for {field.name}: {value}"
                ) from exc
        if field.type in {self._field_descriptor.TYPE_FLOAT, self._field_descriptor.TYPE_DOUBLE}:
            try:
                return float(text)
            except ValueError as exc:
                raise StarlinkClientError(f"Invalid float value for {field.name}: {value}") from exc
        if field.type == self._field_descriptor.TYPE_BYTES:
            return text.encode("utf-8")
        return text

    def _parse_enum(self, field: Any, value: str) -> int:
        enum_values = field.enum_type.values_by_name
        normalized = value.upper().replace("-", "_").replace(" ", "_")
        if normalized in enum_values:
            return enum_values[normalized].number

        suffix_matches = [
            entry.number
            for name, entry in enum_values.items()
            if name.endswith(f"_{normalized}")
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
        if len(suffix_matches) > 1:
            raise StarlinkClientError(f"Ambiguous enum value for {field.name}: {value}")

        try:
            enum_number = int(value, 10)
        except ValueError:
            pass
        else:
            if enum_number in field.enum_type.values_by_number:
                return enum_number

        supported = ", ".join(field.enum_type.values_by_name.keys())
        raise StarlinkClientError(
            f"Invalid enum value for {field.name}: {value}. Supported: {supported}"
        )

    def _serialize_applied_value(self, value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    def _format_error(self, exc: Exception) -> str:
        code_fn = getattr(exc, "code", None)
        details_fn = getattr(exc, "details", None)
        if callable(code_fn) and callable(details_fn):
            try:
                code = code_fn()
                details = details_fn()
                code_name = getattr(code, "name", None) or str(code)
                if code_name.startswith("StatusCode."):
                    code_name = code_name.split(".", 1)[1]
                details_text = str(details).strip()
                return f"{code_name}: {details_text}" if details_text else code_name
            except Exception:  # noqa: BLE001
                pass
        return str(exc)

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
