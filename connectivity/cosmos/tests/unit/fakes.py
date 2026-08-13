from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class FakeHttpError(Exception):
    def __init__(self, status_code: int, private_message: str = 'private') -> None:
        self.status_code = status_code
        super().__init__(private_message)


class FakeMatchConditions:
    IfNotModified = 'if-not-modified'


@dataclass(frozen=True)
class FakePartitionKey:
    path: str
    kind: str = 'Hash'
    version: int = 2


class FakePageIterator:
    def __init__(self, pages: list[list[Any]], tokens: list[str | None]) -> None:
        self.pages = list(pages)
        self.tokens = list(tokens)
        self.index = 0
        self.continuation_token: str | None = None

    def __iter__(self) -> FakePageIterator:
        return self

    def __next__(self) -> list[Any]:
        if self.index >= len(self.pages):
            raise StopIteration
        page = self.pages[self.index]
        self.continuation_token = self.tokens[self.index]
        self.index += 1
        return page


class FakePaged:
    def __init__(
        self,
        values: list[Any],
        *,
        pages: list[list[Any]] | None = None,
        tokens: list[str | None] | None = None,
    ) -> None:
        self.values = values
        self.pages = pages or [values]
        self.tokens = tokens or [None]
        self.received_token: str | None = None

    def __iter__(self):
        return iter(self.values)

    def by_page(self, continuation_token: str | None = None) -> FakePageIterator:
        self.received_token = continuation_token
        return FakePageIterator(self.pages, self.tokens)


class FakeContainer:
    def __init__(self, name: str, properties: dict[str, Any] | None = None) -> None:
        self.name = name
        self.properties = properties or {
            'id': name,
            'partitionKey': {'paths': ['/partition']},
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, Any] = {}
        self.query_paged = FakePaged([])
        self.read_error: BaseException | None = None

    def read(self) -> dict[str, Any]:
        if self.read_error is not None:
            raise self.read_error
        return dict(self.properties)

    def _call(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, kwargs))
        response = self.responses.get(name)
        if isinstance(response, BaseException):
            raise response
        if response is not None:
            return response
        return {'id': kwargs.get('item', kwargs.get('body', {}).get('id', 'item'))}

    def create_item(self, **kwargs: Any) -> Any:
        return self._call('create_item', **kwargs)

    def read_item(self, **kwargs: Any) -> Any:
        return self._call('read_item', **kwargs)

    def upsert_item(self, **kwargs: Any) -> Any:
        return self._call('upsert_item', **kwargs)

    def patch_item(self, **kwargs: Any) -> Any:
        return self._call('patch_item', **kwargs)

    def delete_item(self, **kwargs: Any) -> Any:
        return self._call('delete_item', **kwargs)

    def query_items(self, **kwargs: Any) -> FakePaged:
        self.calls.append(('query_items', kwargs))
        response = self.responses.get('query_items')
        if isinstance(response, BaseException):
            raise response
        return self.query_paged


class FakeDatabase:
    def __init__(self, name: str = 'atlanticus') -> None:
        self.name = name
        self.containers: dict[str, FakeContainer] = {}
        self.read_error: BaseException | None = None
        self.create_calls: list[dict[str, Any]] = []
        self.replace_calls: list[tuple[Any, dict[str, Any]]] = []

    def read(self) -> dict[str, Any]:
        if self.read_error is not None:
            raise self.read_error
        return {'id': self.name}

    def get_container_client(self, name: str) -> FakeContainer:
        return self.containers.setdefault(name, FakeContainer(name))

    def create_container(self, **kwargs: Any) -> FakeContainer:
        self.create_calls.append(kwargs)
        name = kwargs['id']
        if name in self.containers and self.containers[name].read_error is None:
            raise FakeHttpError(409)
        partition_key = kwargs['partition_key']
        properties = {
            'id': name,
            'partitionKey': {
                'paths': [partition_key.path],
                'kind': partition_key.kind,
                'version': partition_key.version,
            },
        }
        if kwargs.get('default_ttl') is not None:
            properties['defaultTtl'] = kwargs['default_ttl']
        container = FakeContainer(name, properties)
        self.containers[name] = container
        return container

    def replace_container(self, container: Any, **kwargs: Any) -> FakeContainer:
        self.replace_calls.append((container, kwargs))
        target = self.get_container_client(container.name)
        target.properties['defaultTtl'] = kwargs.get('default_ttl')
        return target


class FakeClient:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database
        self.database_create_calls: list[str] = []
        self.closed = False
        self.close_error: BaseException | None = None

    def get_database_client(self, name: str) -> FakeDatabase:
        self.database.name = name
        return self.database

    def create_database(self, name: str) -> FakeDatabase:
        self.database_create_calls.append(name)
        self.database.read_error = None
        self.database.name = name
        return self.database

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        self.closed = True
