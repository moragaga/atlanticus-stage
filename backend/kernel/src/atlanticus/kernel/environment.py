"""Valores de ambiente de ejecución utilizados por las aplicaciones Atlanticus."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from atlanticus.kernel.errors import InvalidEnvironmentError

ENVIRONMENT_VARIABLE = 'ENVIRONMENT'


class EnvironmentName(StrEnum):
    """Nombres exactos de ambiente admitidos por Atlanticus."""

    LOCAL = 'local'
    DEV = 'dev'
    UAT = 'uat'
    STG = 'stg'
    PRD = 'prd'


_ALLOWED_VALUES = tuple(item.value for item in EnvironmentName)


@dataclass(frozen=True, slots=True)
class Environment:
    """Ambiente de ejecución validado.

    La clase representa únicamente dónde se ejecuta un proceso. Las identidades de aplicación como
    GE, IO, PR o ST pertenecen a la definición de la aplicación y no deben incluirse en este valor.
    """

    name: EnvironmentName

    def __post_init__(self) -> None:
        if not isinstance(self.name, EnvironmentName):
            raise InvalidEnvironmentError(self.name, _ALLOWED_VALUES)

    @classmethod
    def from_value(
        cls,
        value: EnvironmentName | str | None,
    ) -> Environment:
        """Crea un ambiente desde un nombre oficial exacto.

        El contrato es rígido porque este valor puede participar en la resolución de infraestructura,
        incluidas las conexiones con Key Vault. Son inválidos los valores ausentes, los alias, las
        diferencias entre mayúsculas y minúsculas y los espacios adicionales.
        """

        if isinstance(value, EnvironmentName):
            return cls(name=value)

        try:
            return cls(name=EnvironmentName(value))
        except (TypeError, ValueError) as error:
            raise InvalidEnvironmentError(value, _ALLOWED_VALUES) from error

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str],
    ) -> Environment:
        """Lee el valor oficial de ``ENVIRONMENT`` desde un mapping."""

        return cls.from_value(values.get(ENVIRONMENT_VARIABLE))

    @classmethod
    def from_os(cls) -> Environment:
        """Lee la variable oficial ``ENVIRONMENT`` desde el proceso actual."""

        return cls.from_mapping(os.environ)

    @property
    def is_local(self) -> bool:
        """Indica si el proceso se ejecuta en desarrollo local."""

        return self.name is EnvironmentName.LOCAL

    @property
    def is_uat(self) -> bool:
        """Indica si el proceso se ejecuta en UAT."""

        return self.name is EnvironmentName.UAT

    @property
    def is_stg(self) -> bool:
        """Indica si el proceso se ejecuta en STG."""

        return self.name is EnvironmentName.STG

    @property
    def is_production(self) -> bool:
        """Indica si el proceso se ejecuta en producción."""

        return self.name is EnvironmentName.PRD

    def __str__(self) -> str:
        return self.name.value
