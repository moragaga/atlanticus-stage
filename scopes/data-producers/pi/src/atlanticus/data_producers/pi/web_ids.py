from __future__ import annotations

import json
import os
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from uuid import uuid4

from atlanticus.data_producers.pi.errors import PiDataProducerWebIdRegistryError
from atlanticus.runtime import RuntimeConfiguration

_SCHEMA_VERSION = 1


class WebIdRegistry:
    def __init__(self, *, path: str | Path) -> None:
        self._path = Path(path)
        if not self._path.is_absolute():
            raise PiDataProducerWebIdRegistryError('WebID registry path must be absolute')
        self._lock = Lock()
        self._entries: dict[str, str] | None = None

    @classmethod
    def from_runtime_configuration(
        cls,
        configuration: RuntimeConfiguration,
        *,
        producer_key: str = 'pi-web-api',
    ) -> WebIdRegistry:
        if not isinstance(configuration, RuntimeConfiguration):
            raise TypeError('configuration must be a RuntimeConfiguration')
        if not isinstance(producer_key, str) or not producer_key.strip():
            raise ValueError('producer_key must be non-empty text')
        if producer_key != producer_key.strip():
            raise ValueError('producer_key must not contain surrounding whitespace')
        return cls(path=configuration.runtime_root / 'cache' / producer_key / 'webids.json')

    @property
    def path(self) -> Path:
        return self._path

    def current(self) -> Mapping[str, str]:
        with self._lock:
            if self._entries is None:
                self._entries = self._read()
            return MappingProxyType(dict(self._entries))

    def lookup(self, tag_names: tuple[str, ...]) -> Mapping[str, str]:
        if not isinstance(tag_names, tuple) or any(
            not isinstance(item, str) or not item for item in tag_names
        ):
            raise PiDataProducerWebIdRegistryError(
                'tag_names must be a tuple of non-empty text values'
            )
        entries = self.current()
        return MappingProxyType({name: entries[name] for name in tag_names if name in entries})

    def merge(self, entries: Mapping[str, str]) -> Mapping[str, str]:
        normalized = _normalize_entries(entries)
        if not normalized:
            return self.current()
        with self._lock:
            if self._entries is None:
                self._entries = self._read()
            updated = dict(self._entries)
            changed = False
            for tag_name, web_id in normalized.items():
                if updated.get(tag_name) == web_id:
                    continue
                updated[tag_name] = web_id
                changed = True
            if changed:
                self._replace(updated)
                self._entries = updated
            return MappingProxyType(dict(self._entries))

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise PiDataProducerWebIdRegistryError('WebID registry could not be read') from error
        if not isinstance(payload, Mapping):
            raise PiDataProducerWebIdRegistryError('WebID registry must be a JSON object')
        if set(payload) != {'schema_version', 'web_ids'}:
            raise PiDataProducerWebIdRegistryError(
                'WebID registry has unexpected or missing fields'
            )
        if payload.get('schema_version') != _SCHEMA_VERSION:
            raise PiDataProducerWebIdRegistryError('WebID registry schema version is not supported')
        web_ids = payload.get('web_ids')
        if not isinstance(web_ids, Mapping):
            raise PiDataProducerWebIdRegistryError('WebID registry web_ids must be a JSON object')
        return _normalize_entries(web_ids)

    def _replace(self, entries: Mapping[str, str]) -> None:
        payload = {
            'schema_version': _SCHEMA_VERSION,
            'web_ids': dict(sorted(entries.items())),
        }
        content = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        temporary_path = self._path.with_name(f'.{self._path.name}.{uuid4().hex}.tmp')
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o640,
            )
            with os.fdopen(descriptor, 'wb') as file_handle:
                file_handle.write(content)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temporary_path, self._path)
            _fsync_directory(self._path.parent)
        except OSError as error:
            raise PiDataProducerWebIdRegistryError('WebID registry could not be written') from error
        finally:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _normalize_entries(entries: Mapping[str, object]) -> dict[str, str]:
    if not isinstance(entries, Mapping):
        raise PiDataProducerWebIdRegistryError('WebID entries must be a mapping')
    normalized: dict[str, str] = {}
    seen: set[str] = set()
    for raw_tag_name, raw_web_id in entries.items():
        if not isinstance(raw_tag_name, str) or not raw_tag_name:
            raise PiDataProducerWebIdRegistryError(
                'WebID registry tag names must be non-empty text'
            )
        if raw_tag_name != raw_tag_name.strip():
            raise PiDataProducerWebIdRegistryError(
                'WebID registry tag names must not contain surrounding whitespace'
            )
        if not isinstance(raw_web_id, str) or not raw_web_id:
            raise PiDataProducerWebIdRegistryError('WebID registry values must be non-empty text')
        if raw_web_id != raw_web_id.strip():
            raise PiDataProducerWebIdRegistryError(
                'WebID registry values must not contain surrounding whitespace'
            )
        key = raw_tag_name.casefold()
        if key in seen:
            raise PiDataProducerWebIdRegistryError('WebID registry contains duplicate tag names')
        seen.add(key)
        normalized[raw_tag_name] = raw_web_id
    return normalized


def _fsync_directory(path: Path) -> None:
    if os.name == 'nt':
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
