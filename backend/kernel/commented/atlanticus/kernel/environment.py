"""Valores de ambiente de ejecución utilizados por las aplicaciones Atlanticus."""

from __future__ import annotations

# ``os`` se usa solamente para leer el ambiente del proceso; el kernel no carga archivos .env.
import os

# ``Mapping`` permite probar el contrato con un diccionario sin modificar variables del proceso.
from collections.abc import Mapping

# La configuración se vuelve inmutable mediante una dataclass pequeña.
from dataclasses import dataclass

# ``StrEnum`` conserva valores de texto serializables y un conjunto cerrado.
from enum import StrEnum

from atlanticus.kernel.errors import InvalidEnvironmentError

# Existe un único nombre oficial para esta variable en todo Atlanticus.
ENVIRONMENT_VARIABLE = 'ENVIRONMENT'


class EnvironmentName(StrEnum):
    """Nombres exactos de ambiente admitidos por Atlanticus."""

    # Los valores son deliberadamente rígidos. UAT y STG se conservan como entradas diferentes.
    LOCAL = 'local'
    DEV = 'dev'
    UAT = 'uat'
    STG = 'stg'
    PRD = 'prd'


# Esta tupla se calcula desde el enum para que el mensaje de error y el contrato no diverjan.
_ALLOWED_VALUES = tuple(item.value for item in EnvironmentName)


# ``frozen`` impide cambiar el ambiente después del arranque y ``slots`` evita atributos ajenos.
@dataclass(frozen=True, slots=True)
class Environment:
    """Ambiente de ejecución validado.

    La clase representa únicamente dónde se ejecuta un proceso. Las identidades de aplicación como
    GE, IO, PR o ST pertenecen a la definición de la aplicación y no deben incluirse en este valor.
    """

    name: EnvironmentName

    def __post_init__(self) -> None:
        # La anotación no valida en runtime; este control también protege el constructor directo.
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

        # Si ya se recibió un miembro oficial, no es necesario volver a construirlo.
        if isinstance(value, EnvironmentName):
            return cls(name=value)

        try:
            # El enum compara el texto exacto: no aplica lower, strip, alias ni valor por defecto.
            return cls(name=EnvironmentName(value))
        except (TypeError, ValueError) as error:
            # Se conserva el error original como causa y se expone el error público del kernel.
            raise InvalidEnvironmentError(value, _ALLOWED_VALUES) from error

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, str],
    ) -> Environment:
        """Lee el valor oficial de ``ENVIRONMENT`` desde un mapping."""

        # ``get`` retorna None cuando falta la variable; ``from_value`` lo rechaza explícitamente.
        return cls.from_value(values.get(ENVIRONMENT_VARIABLE))

    @classmethod
    def from_os(cls) -> Environment:
        """Lee la variable oficial ``ENVIRONMENT`` desde el proceso actual."""

        # Toda la validación permanece centralizada en ``from_mapping`` y ``from_value``.
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
        # Al convertir la instancia a texto se entrega el valor oficial, no la representación enum.
        return self.name.value
