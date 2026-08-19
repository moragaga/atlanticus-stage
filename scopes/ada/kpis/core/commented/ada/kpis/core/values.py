# Centraliza los tipos nativos permitidos entre resolvers, engine, latest y evaluation.
# Los resolvers no serializan JSON ni hacen transformaciones de presentación.
from __future__ import annotations

type KpiScalar = str | int | float
type KpiJsonValue = str | int | float | bool | None | list[KpiJsonValue] | dict[str, KpiJsonValue]
type KpiJsonContainer = list[KpiJsonValue] | dict[str, KpiJsonValue]
type KpiNativeValue = KpiScalar | KpiJsonContainer | None
