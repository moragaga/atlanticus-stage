# Centraliza la construcción de columnas y la zona horaria de fechas fuente.
from __future__ import annotations

from ada.processes.blockgrade.models import BlockgradeColumnDefinition, BlockgradeValueKind

SOURCE_TIMEZONE = 'America/Santiago'


def column(
    *,
    source_name: str,
    output_name: str,
    value_kind: BlockgradeValueKind,
    required: bool,
) -> BlockgradeColumnDefinition:
    return BlockgradeColumnDefinition(
        source_name=source_name,
        output_name=output_name,
        value_kind=value_kind,
        required=required,
        source_timezone=(SOURCE_TIMEZONE if value_kind is BlockgradeValueKind.DATETIME else None),
    )
