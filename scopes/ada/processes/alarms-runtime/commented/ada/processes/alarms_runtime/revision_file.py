# Este módulo contiene únicamente adapters locales sobre JSON atómico.
# El layout de cache sí es contractual; el layout revisionado de Source es un POC local.
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ada.processes.alarms_runtime.revision_resolution import (
    RuntimeManifest,
    RuntimeRevisionBundle,
    RuntimeRevisionCacheError,
    RuntimeRevisionDocument,
    RuntimeRevisionSourceError,
)
from atlanticus.state import AtomicJsonStore, StateError

# Source local conserva un archivo por revisión para respetar lecturas exactas AC/TR.
_SOURCE_MANIFEST_PATH = 'runtime-manifest.json'
_SOURCE_ALARM_CONFIGURATION_DIRECTORY = 'alarm-configuration'
_SOURCE_TOOL_REGISTRY_DIRECTORY = 'tool-registry'
# El cache usa exactamente el namespace operacional cerrado en A03.2.
_CACHE_MANIFEST_PATH = 'runtime/cache/runtime-manifest.json'
_CACHE_ALARM_CONFIGURATION_PATH = 'runtime/cache/alarm-configuration.json'
_CACHE_TOOL_REGISTRY_PATH = 'runtime/cache/tool-registry.json'


@dataclass(slots=True)
class FileRuntimeRevisionSource:
    root_path: str | Path
    _store: AtomicJsonStore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._store = AtomicJsonStore(root_path=self.root_path)

    def read_manifest(self) -> RuntimeManifest:
        document = self._read_required(_SOURCE_MANIFEST_PATH, label='runtime manifest')
        try:
            return _decode_manifest(document)
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeRevisionSourceError('published runtime manifest is invalid') from error

    def read_alarm_configuration(self, *, revision: str) -> RuntimeRevisionDocument:
        return self._read_revision_document(
            directory=_SOURCE_ALARM_CONFIGURATION_DIRECTORY,
            revision=revision,
            label='alarm configuration',
        )

    def read_tool_registry(self, *, revision: str) -> RuntimeRevisionDocument:
        return self._read_revision_document(
            directory=_SOURCE_TOOL_REGISTRY_DIRECTORY,
            revision=revision,
            label='tool registry',
        )

    def _read_revision_document(
        self,
        *,
        directory: str,
        revision: str,
        label: str,
    ) -> RuntimeRevisionDocument:
        # El ID se usa sólo como componente de nombre seguro dentro del adapter local.
        filename = _revision_filename(revision)
        return self._read_required(f'{directory}/{filename}', label=label)

    def _read_required(self, relative_path: str, *, label: str) -> RuntimeRevisionDocument:
        try:
            document = self._store.read(relative_path)
        except StateError as error:
            raise RuntimeRevisionSourceError(f'could not read published {label}') from error
        if document is None:
            raise RuntimeRevisionSourceError(f'published {label} does not exist')
        return document


@dataclass(slots=True)
class FileRuntimeRevisionCache:
    root_path: str | Path
    _store: AtomicJsonStore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._store = AtomicJsonStore(root_path=self.root_path)

    def load_effective(self) -> RuntimeRevisionBundle | None:
        try:
            # Los tres documentos se consideran un conjunto lógico.
            manifest_document = self._store.read(_CACHE_MANIFEST_PATH)
            alarm_configuration = self._store.read(_CACHE_ALARM_CONFIGURATION_PATH)
            tool_registry = self._store.read(_CACHE_TOOL_REGISTRY_PATH)
        except StateError as error:
            raise RuntimeRevisionCacheError(
                'could not read effective runtime revision cache'
            ) from error
        existing_count = sum(
            document is not None
            for document in (manifest_document, alarm_configuration, tool_registry)
        )
        # Cache completamente vacío corresponde al bootstrap físico.
        if existing_count == 0:
            return None
        # Un conjunto parcial nunca se interpreta ni se reconstruye heurísticamente.
        if existing_count != 3:
            raise RuntimeRevisionCacheError('effective runtime revision cache is incomplete')
        try:
            manifest = _decode_manifest(manifest_document)
            return RuntimeRevisionBundle(
                manifest=manifest,
                alarm_configuration=alarm_configuration,
                tool_registry=tool_registry,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeRevisionCacheError(
                'effective runtime revision cache is invalid'
            ) from error

    def replace_effective(self, *, bundle: RuntimeRevisionBundle) -> None:
        if not isinstance(bundle, RuntimeRevisionBundle):
            raise TypeError('bundle must be a RuntimeRevisionBundle')
        try:
            # Los documentos pesados se reemplazan primero.
            self._store.replace(_CACHE_ALARM_CONFIGURATION_PATH, bundle.alarm_configuration)
            self._store.replace(_CACHE_TOOL_REGISTRY_PATH, bundle.tool_registry)
            # El manifest se reemplaza al final y actúa como commit marker local.
            self._store.replace(_CACHE_MANIFEST_PATH, _encode_manifest(bundle.manifest))
        except StateError as error:
            raise RuntimeRevisionCacheError(
                'could not replace effective runtime revision cache'
            ) from error


def _revision_filename(revision: str) -> str:
    if not isinstance(revision, str) or not revision.strip():
        raise RuntimeRevisionSourceError('runtime revision must be a non-empty string')
    normalized = revision.strip()
    if normalized in {'.', '..'} or '/' in normalized or '\\' in normalized:
        raise RuntimeRevisionSourceError('runtime revision is not a safe file name')
    return f'{normalized}.json'


# Manifest se parsea una sola vez para Source y Cache, conservando el contrato R3.4A.
def _decode_manifest(document: RuntimeRevisionDocument) -> RuntimeManifest:
    return RuntimeManifest(
        schema_version=document['schema_version'],
        alarm_configuration_revision=document['alarm_configuration_revision'],
        tool_registry_revision=document['tool_registry_revision'],
        published_at=_parse_datetime(document['published_at']),
    )


def _encode_manifest(manifest: RuntimeManifest) -> dict[str, object]:
    return {
        'schema_version': manifest.schema_version,
        'alarm_configuration_revision': manifest.alarm_configuration_revision,
        'tool_registry_revision': manifest.tool_registry_revision,
        'published_at': manifest.published_at.isoformat().replace('+00:00', 'Z'),
    }


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError('published_at must be a string')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))
