from __future__ import annotations

from ada.kpis.core import KpiSource


class KpiSourcesError(Exception):
    pass


class KpiSourceBindingError(KpiSourcesError):
    pass


class KpiSourceReadError(KpiSourcesError):
    pass


class KpiSourceSchemaError(KpiSourcesError):
    pass


class KpiSourceUnavailableError(KpiSourcesError):
    def __init__(self, source: KpiSource, message: str) -> None:
        if not isinstance(source, KpiSource):
            raise TypeError('source must be KpiSource')
        self.source = source
        super().__init__(f'{source.value}: {message}')
