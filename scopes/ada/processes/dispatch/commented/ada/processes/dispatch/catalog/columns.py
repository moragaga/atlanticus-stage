# Centraliza la construcción de columnas y la zona horaria de fechas fuente.
from __future__ import annotations

from ada.processes.dispatch.models import DispatchColumnDefinition, DispatchValueKind

SOURCE_TIMEZONE = 'America/Santiago'


def column(
    *,
    source_name: str,
    output_name: str,
    value_kind: DispatchValueKind,
    required: bool,
) -> DispatchColumnDefinition:
    return DispatchColumnDefinition(
        source_name=source_name,
        output_name=output_name,
        value_kind=value_kind,
        required=required,
        source_timezone=(SOURCE_TIMEZONE if value_kind is DispatchValueKind.DATETIME else None),
    )
