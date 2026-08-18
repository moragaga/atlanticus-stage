from __future__ import annotations

from atlanticus.data_producers.sql import DataValueKind, SqlColumnDefinition

SOURCE_TIMEZONE = 'America/Santiago'


def column(
    *,
    source_name: str,
    output_name: str,
    value_kind: DataValueKind,
    required: bool,
) -> SqlColumnDefinition:
    return SqlColumnDefinition(
        source_name=source_name,
        output_name=output_name,
        value_kind=value_kind,
        required=required,
        source_timezone=(SOURCE_TIMEZONE if value_kind is DataValueKind.DATETIME else None),
    )
