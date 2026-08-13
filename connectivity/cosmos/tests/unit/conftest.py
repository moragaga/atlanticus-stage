from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable


@dataclass(frozen=True)
class ErrorInfo:
    error_type: str
    message: str


@dataclass(frozen=True)
class ResultSummary:
    attributes: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


def runtime_guard(**_: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
        return function

    return decorator


module = ModuleType('atlanticus.observability')
module.ErrorInfo = ErrorInfo
module.ResultSummary = ResultSummary
module.runtime_guard = runtime_guard
sys.modules.setdefault('atlanticus.observability', module)
