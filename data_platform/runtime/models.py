from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class Run:
    id: str
    workspace_id: str
    kind: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    version: int = 0


@dataclass(frozen=True)
class RunEvent:
    id: str
    run_id: str
    step_id: str
    kind: str
    created_at: datetime
    payload: dict[str, Any]
    sequence: int = 0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

