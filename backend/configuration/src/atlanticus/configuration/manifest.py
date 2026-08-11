"""Lectura estricta del manifiesto corporativo ``secrets.json``."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from atlanticus.configuration.errors import SecretsManifestError
from atlanticus.configuration.models import validate_variable_name
from atlanticus.kernel import ENVIRONMENT_VARIABLE

_REQUIRED_FIELDS = frozenset({'var_name', 'secret_name', 'value', 'exists_in_key_vault'})


@dataclass(frozen=True, slots=True, repr=False)
class SecretManifestEntry:
    """Entrada validada del formato corporativo existente."""

    var_name: str
    secret_name: str | None
    value: str | None
    exists_in_key_vault: bool

    def __post_init__(self) -> None:
        if not isinstance(self.var_name, str):
            raise SecretsManifestError('Secrets manifest var_name must be a string.')
        try:
            var_name = validate_variable_name(self.var_name)
        except ValueError as error:
            raise SecretsManifestError(str(error)) from None
        if var_name == ENVIRONMENT_VARIABLE:
            raise SecretsManifestError(
                f"Secrets manifest cannot declare reserved variable '{ENVIRONMENT_VARIABLE}'."
            )
        if type(self.exists_in_key_vault) is not bool:
            raise SecretsManifestError(
                f"Secrets manifest variable '{var_name}' must define "
                'exists_in_key_vault as a boolean.'
            )
        secret_name = _optional_secret_name(
            self.secret_name,
            variable_name=var_name,
        )
        value = _optional_value(
            self.value,
            variable_name=var_name,
        )
        if self.exists_in_key_vault and secret_name is None:
            raise SecretsManifestError(
                f"Secrets manifest variable '{var_name}' requires secret_name."
            )
        if not self.exists_in_key_vault and value is None:
            raise SecretsManifestError(
                f"Secrets manifest variable '{var_name}' requires a non-empty value."
            )
        object.__setattr__(self, 'var_name', var_name)
        object.__setattr__(self, 'secret_name', secret_name)
        object.__setattr__(self, 'value', value)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, index: int) -> SecretManifestEntry:
        """Construye una entrada sin admitir campos obligatorios implícitos."""

        missing_fields = _REQUIRED_FIELDS.difference(data)
        if missing_fields:
            fields = ', '.join(sorted(missing_fields))
            raise SecretsManifestError(
                f'Secrets manifest entry {index} is missing required fields: {fields}.'
            )

        unknown_fields = set(data).difference(_REQUIRED_FIELDS)
        if unknown_fields:
            fields = ', '.join(sorted(repr(field) for field in unknown_fields))
            raise SecretsManifestError(
                f'Secrets manifest entry {index} contains unknown fields: {fields}.'
            )

        raw_var_name = data['var_name']
        if not isinstance(raw_var_name, str):
            raise SecretsManifestError(f'Secrets manifest entry {index} has an invalid var_name.')
        try:
            var_name = validate_variable_name(raw_var_name)
        except ValueError as error:
            raise SecretsManifestError(str(error)) from None

        return cls(
            var_name=var_name,
            secret_name=data['secret_name'],
            value=data['value'],
            exists_in_key_vault=data['exists_in_key_vault'],
        )

    def __repr__(self) -> str:
        return (
            f'SecretManifestEntry(var_name={self.var_name!r}, '
            f'exists_in_key_vault={self.exists_in_key_vault!r})'
        )


@dataclass(frozen=True, slots=True)
class SecretsManifest:
    """Colección inmutable e indexada de variables declaradas por un contenedor."""

    entries: tuple[SecretManifestEntry, ...]
    _by_variable: Mapping[str, SecretManifestEntry] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            raise SecretsManifestError('Secrets manifest entries must be a tuple.')
        by_variable: dict[str, SecretManifestEntry] = {}
        for entry in self.entries:
            if not isinstance(entry, SecretManifestEntry):
                raise SecretsManifestError(
                    'Secrets manifest entries must contain SecretManifestEntry instances.'
                )
            if entry.var_name in by_variable:
                raise SecretsManifestError(
                    f"Secrets manifest contains duplicate variable '{entry.var_name}'."
                )
            by_variable[entry.var_name] = entry
        object.__setattr__(self, '_by_variable', MappingProxyType(by_variable))

    @classmethod
    def from_path(cls, path: str | Path) -> SecretsManifest:
        """Lee un archivo obligatorio y valida su contenido completo."""

        try:
            manifest_path = Path(path)
        except TypeError:
            raise SecretsManifestError('Secrets manifest path must be a string or Path.') from None
        try:
            is_file = manifest_path.is_file()
        except OSError:
            raise SecretsManifestError(
                f'Secrets manifest could not be read: {manifest_path}.'
            ) from None
        if not is_file:
            raise SecretsManifestError(f'Secrets manifest was not found: {manifest_path}.')
        try:
            document = json.loads(manifest_path.read_text(encoding='utf-8'))
        except OSError, UnicodeError, json.JSONDecodeError:
            raise SecretsManifestError(
                f'Secrets manifest could not be read: {manifest_path}.'
            ) from None
        if not isinstance(document, list):
            raise SecretsManifestError('Secrets manifest root must be a JSON array.')

        entries: list[SecretManifestEntry] = []
        for index, item in enumerate(document):
            if not isinstance(item, Mapping):
                raise SecretsManifestError(f'Secrets manifest entry {index} must be a JSON object.')
            entry = SecretManifestEntry.from_mapping(item, index=index)
            entries.append(entry)

        return cls(entries=tuple(entries))

    def find(self, var_name: str) -> SecretManifestEntry | None:
        """Busca una variable sin exponer el índice mutable interno."""

        return self._by_variable.get(var_name)

    def static_values(self) -> Mapping[str, str]:
        """Entrega solo valores no secretos para componer infraestructura."""

        return MappingProxyType(
            {
                entry.var_name: entry.value
                for entry in self.entries
                if not entry.exists_in_key_vault and entry.value is not None
            }
        )


def _optional_secret_name(
    value: object,
    *,
    variable_name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SecretsManifestError(
            f"Secrets manifest variable '{variable_name}' must define secret_name "
            'as a string or null.'
        )
    if not value.strip():
        return None
    return value


def _optional_value(
    value: object,
    *,
    variable_name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SecretsManifestError(
            f"Secrets manifest variable '{variable_name}' must define value "
            'as a string or null.'
        )
    if value == '':
        return None
    return value
