from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Telemetry:
    fields: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.fields)


@dataclass(slots=True)
class AppliedResult:
    requested: str
    applied: str | None
    success: bool
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
