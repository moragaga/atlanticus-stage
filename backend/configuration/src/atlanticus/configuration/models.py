"""Modelos inmutables de configuración resuelta."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from atlanticus.configuration.errors import ConfigurationValueError
from atlanticus.kernel import ENVIRONMENT_VARIABLE, Environment

_VARIABLE_NAME_PATTERN = re.compile(r'^[A-Z][A-Z0-9_]*$')
_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})
_FALSE_VALUES = frozenset({'0', 'false', 'no', 'off'})


class ConfigurationSource(StrEnum):
    """Fuentes autorizadas de un valor de configuración."""

    PROCESS = 'process'
    DOTENV = 'dotenv'
    MANIFEST = 'manifest'
    KEY_VAULT = 'key_vault'
    DEFAULT = 'default'


@dataclass(frozen=True, slots=True)
class ConfigurationVariableSpec:
    """Contrato esperado para una variable consumida por el proceso."""

    key: str
    required: bool = True
    default: str | None = None
    sensitive: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, str):
            raise ConfigurationValueError('Configuration variable key must be a string.')
        if _VARIABLE_NAME_PATTERN.fullmatch(self.key) is None:
            raise ConfigurationValueError(f'Invalid environment variable name: {self.key!r}.')
        if type(self.required) is not bool:
            raise ConfigurationValueError(
                f"Environment variable '{self.key}' must define required as a boolean."
            )
        if type(self.sensitive) is not bool:
            raise ConfigurationValueError(
                f"Environment variable '{self.key}' must define sensitive as a boolean."
            )
        normalized_default = _normalize_optional_text(self.default)
        if self.sensitive and normalized_default is not None:
            raise ConfigurationValueError(
                f"Sensitive environment variable '{self.key}' cannot define a default value."
            )
        object.__setattr__(self, 'default', normalized_default)


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedConfiguration:
    """Vista inmutable de valores ya resueltos y validados."""

    environment: Environment
    values: Mapping[str, str]
    sources: Mapping[str, ConfigurationSource]
    sensitive_keys: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise ConfigurationValueError(
                'Resolved configuration environment must be an Environment instance.'
            )
        if not isinstance(self.values, Mapping):
            raise ConfigurationValueError('Configuration values must be a mapping.')
        if not isinstance(self.sources, Mapping):
            raise ConfigurationValueError('Configuration sources must be a mapping.')

        copied_values = MappingProxyType(dict(self.values))
        copied_sources = MappingProxyType(dict(self.sources))
        if copied_values.keys() != copied_sources.keys():
            raise ConfigurationValueError(
                'Configuration values and sources must contain the same keys.'
            )

        for key, value in copied_values.items():
            if not isinstance(key, str) or _VARIABLE_NAME_PATTERN.fullmatch(key) is None:
                raise ConfigurationValueError(f'Invalid environment variable name: {key!r}.')
            if not isinstance(value, str):
                raise ConfigurationValueError(
                    f"Environment variable '{key}' must contain a string value."
                )

        for key, source in copied_sources.items():
            if not isinstance(source, ConfigurationSource):
                raise ConfigurationValueError(
                    f"Environment variable '{key}' has an invalid configuration source."
                )

        environment_value = copied_values.get(ENVIRONMENT_VARIABLE)
        if environment_value is None:
            raise ConfigurationValueError(
                f"Resolved configuration must contain environment variable '{ENVIRONMENT_VARIABLE}'."
            )
        if environment_value != str(self.environment):
            raise ConfigurationValueError(
                f"Resolved environment variable '{ENVIRONMENT_VARIABLE}' "
                'does not match the environment.'
            )

        try:
            copied_sensitive_keys = frozenset(self.sensitive_keys)
        except TypeError:
            raise ConfigurationValueError(
                'Sensitive configuration keys must be iterable.'
            ) from None
        if any(not isinstance(key, str) for key in copied_sensitive_keys):
            raise ConfigurationValueError('Sensitive configuration keys must be strings.')
        unknown_sensitive_keys = copied_sensitive_keys.difference(copied_values)
        if unknown_sensitive_keys:
            unknown = ', '.join(sorted(unknown_sensitive_keys))
            raise ConfigurationValueError(
                f'Sensitive configuration keys are not resolved: {unknown}.'
            )

        object.__setattr__(self, 'values', copied_values)
        object.__setattr__(self, 'sources', copied_sources)
        object.__setattr__(self, 'sensitive_keys', copied_sensitive_keys)

    def __repr__(self) -> str:
        keys = ', '.join(sorted(self.values))
        return f'ResolvedConfiguration(environment={str(self.environment)!r}, keys=[{keys}])'

    def get(self, key: str, default: str | None = None) -> str | None:
        """Obtiene un valor opcional sin alterar la configuración."""

        return self.values.get(key, default)

    def require(self, key: str) -> str:
        """Obtiene un valor obligatorio indicando su variable exacta si falta."""

        value = self.values.get(key)
        if value is None or value == '':
            raise ConfigurationValueError(f"Required environment variable '{key}' is missing.")
        return value

    def get_bool(self, key: str, default: bool | None = None) -> bool | None:
        """Convierte un valor booleano mediante un contrato estricto."""

        value = self.get(key)
        if value is None:
            return default
        normalized = value.lower()
        if normalized in _TRUE_VALUES:
            return True
        if normalized in _FALSE_VALUES:
            return False
        raise ConfigurationValueError(
            f"Environment variable '{key}' must contain a valid boolean value."
        )

    def get_int(self, key: str, default: int | None = None) -> int | None:
        """Convierte un entero y mantiene el error asociado a la variable."""

        value = self.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            raise ConfigurationValueError(
                f"Environment variable '{key}' must contain a valid integer value."
            ) from None

    def to_dict(self, *, mask_sensitive: bool = True) -> dict[str, str]:
        """Entrega una copia opcionalmente enmascarada para diagnóstico seguro."""

        if not mask_sensitive:
            return dict(self.values)
        return {
            key: '***' if key in self.sensitive_keys else value
            for key, value in self.values.items()
        }


def validate_variable_name(value: str) -> str:
    """Valida y devuelve un nombre de variable exacto."""

    if _VARIABLE_NAME_PATTERN.fullmatch(value) is None:
        raise ConfigurationValueError(f'Invalid environment variable name: {value!r}.')
    return value


def normalize_configuration_value(value: object) -> str | None:
    """Valida valores de texto sin alterar su contenido."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationValueError('Configuration values must be strings.')
    if value == '':
        return None
    return value


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationValueError('Configuration defaults must be strings.')
    if value == '':
        return None
    return value
