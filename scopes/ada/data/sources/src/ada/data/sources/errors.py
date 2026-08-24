from __future__ import annotations

from ada.data.core import DataSource


class DataSourcesError(Exception):
    pass


class DataSourceBindingError(DataSourcesError):
    pass


class DataSourceReadError(DataSourcesError):
    pass


class DataSourceRoutingError(DataSourcesError):
    pass


class DataSourceSchemaError(DataSourcesError):
    pass


class DataSourceUnavailableError(DataSourcesError):
    def __init__(self, source: DataSource, message: str) -> None:
        if not isinstance(source, DataSource):
            raise TypeError('source must be DataSource')
        self.source = source
        super().__init__(f'{source.value}: {message}')
