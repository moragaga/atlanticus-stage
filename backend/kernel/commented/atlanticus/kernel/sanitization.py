"""Conversión de valores operacionales en datos acotados y seguros para JSON."""

from __future__ import annotations

# Las importaciones pertenecen a la biblioteca estándar; el kernel no depende de un serializador.
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

# Se buscan fragmentos y no únicamente nombres completos para cubrir campos como access_token.
DEFAULT_SENSITIVE_KEY_PARTS = (
    'password',
    'passwd',
    'pwd',
    'secret',
    'token',
    'credential',
    'connection_string',
    'access_key',
    'api_key',
    'account_key',
    'authorization',
    'sas',
    'shared_access_signature',
)

# Una constante pública evita que cada consumidor invente su propia marca de redacción.
REDACTED = '***redacted***'


def _normalize_sensitive_key(value: str) -> str:
    # Ignorar mayúsculas y separadores permite comparar snake_case, camelCase y kebab-case igual.
    return ''.join(character for character in value.casefold() if character.isalnum())


class DataSanitizer:
    """Prepara valores acotados para logs, eventos y diagnósticos.

    El sanitizador no cifra datos ni reemplaza la seguridad de la aplicación. Su objetivo es reducir
    la exposición accidental de secretos y evitar payloads de diagnóstico demasiado grandes.
    """

    def __init__(
        self,
        *,
        max_depth: int = 4,
        max_items: int = 50,
        max_string_length: int = 500,
        sensitive_key_parts: tuple[str, ...] = DEFAULT_SENSITIVE_KEY_PARTS,
    ) -> None:
        # Los límites se validan al construir la instancia para fallar antes de procesar payloads.
        if max_depth < 0:
            raise ValueError('max_depth must be greater than or equal to zero.')
        if max_items < 1:
            raise ValueError('max_items must be greater than zero.')
        if max_string_length < 1:
            raise ValueError('max_string_length must be greater than zero.')
        if not sensitive_key_parts:
            raise ValueError('sensitive_key_parts must not be empty.')

        # Los fragmentos personalizados se validan y normalizan una sola vez al construir la clase.
        normalized_sensitive_key_parts: list[str] = []
        for part in sensitive_key_parts:
            if not isinstance(part, str) or not part.strip():
                raise ValueError('sensitive_key_parts must contain only non-empty strings.')
            normalized_part = _normalize_sensitive_key(part)
            if not normalized_part:
                raise ValueError('sensitive_key_parts must contain only non-empty strings.')
            normalized_sensitive_key_parts.append(normalized_part)

        self.max_depth = max_depth
        self.max_items = max_items
        self.max_string_length = max_string_length
        self.sensitive_key_parts = tuple(normalized_sensitive_key_parts)

    def sanitize(self, value: Any, *, key: str | None = None) -> Any:
        """Retorna una representación de ``value`` segura para JSON."""

        # El método público inicia la recursión con toda la profundidad configurada.
        return self._sanitize(value, key=key, depth=self.max_depth)

    def is_sensitive_key(self, key: str) -> bool:
        """Indica si el nombre de un campo parece sensible."""

        normalized = _normalize_sensitive_key(key)
        return any(part in normalized for part in self.sensitive_key_parts)

    def _sanitize(self, value: Any, *, key: str | None, depth: int) -> Any:
        # La redacción ocurre antes de inspeccionar el valor para no filtrar su tipo o contenido.
        if key is not None and self.is_sensitive_key(key):
            return REDACTED

        # None, booleanos y enteros ya son valores seguros para JSON.
        if value is None or isinstance(value, bool | int):
            return value

        # Los floats requieren tratamiento especial para NaN e infinitos.
        if isinstance(value, float):
            return self._sanitize_float(value)

        if isinstance(value, str):
            return self._truncate(value)

        # ISO 8601 mantiene fechas y horas legibles y serializables.
        if isinstance(value, datetime | date | time):
            return value.isoformat()

        # Una duración se expresa como cantidad total de segundos.
        if isinstance(value, timedelta):
            return value.total_seconds()

        # Estos tipos cuentan con una representación de texto estable y segura para el contrato.
        if isinstance(value, Decimal | UUID | Path):
            return str(value)

        # Se sanitiza el valor del enum en vez de su nombre o representación interna.
        if isinstance(value, Enum):
            return self._sanitize(value.value, key=key, depth=depth)

        # Los bytes no se decodifican ni se incluyen; sólo se informa tipo y tamaño.
        if isinstance(value, bytes | bytearray | memoryview):
            return {
                'type': type(value).__name__,
                'size_bytes': len(value),
            }

        # Ni siquiera el mensaje se conserva: los SDK pueden insertar credenciales o URLs firmadas.
        if isinstance(value, BaseException):
            return {'type': type(value).__name__}

        # El límite se evalúa antes de descender en estructuras compuestas.
        if depth < 0:
            return {
                'type': type(value).__name__,
                'summary': 'max_depth_reached',
            }

        # Las dataclasses se recorren por sus campos declarados, no mediante __dict__.
        if is_dataclass(value) and not isinstance(value, type):
            return self._sanitize_dataclass(value, depth=depth)

        if isinstance(value, Mapping):
            return self._sanitize_mapping(value, depth=depth)

        if isinstance(value, list | tuple | set | frozenset):
            return self._sanitize_collection(value, depth=depth)

        # No se ejecuta repr sobre objetos desconocidos porque podría exponer secretos o ser costoso.
        return {
            'type': type(value).__name__,
            'summary': 'unsupported_object',
        }

    def _sanitize_dataclass(self, value: Any, *, depth: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for index, field in enumerate(fields(value)):
            # El marcador indica que el resultado fue limitado intencionalmente.
            if index >= self.max_items:
                result['__truncated__'] = True
                break
            # El nombre del campo se entrega como key para aplicar redacción sensible recursiva.
            result[field.name] = self._sanitize(
                getattr(value, field.name),
                key=field.name,
                depth=depth - 1,
            )
        return result

    def _sanitize_mapping(self, value: Mapping[Any, Any], *, depth: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for index, (item_key, item_value) in enumerate(value.items()):
            if index >= self.max_items:
                result['__truncated__'] = True
                break
            # Los mappings operacionales usan nombres JSON. Una clave de otro tipo degrada todo el
            # mapping sin ejecutar str ni repr sobre un objeto potencialmente sensible.
            if not isinstance(item_key, str):
                return {
                    'type': type(value).__name__,
                    'summary': 'non_string_key',
                }
            # Los nombres string se conservan completos para evaluar correctamente su sensibilidad.
            result[item_key] = self._sanitize(
                item_value,
                key=item_key,
                depth=depth - 1,
            )
        return result

    def _sanitize_collection(
        self,
        value: list[Any] | tuple[Any, ...] | set[Any] | frozenset[Any],
        *,
        depth: int,
    ) -> list[Any]:
        # Todas las colecciones soportadas se entregan como lista para facilitar su JSON final.
        values = list(value)
        result = [
            self._sanitize(item, key=None, depth=depth - 1) for item in values[: self.max_items]
        ]
        if len(values) > self.max_items:
            result.append({'__truncated__': True})
        return result

    def _sanitize_float(self, value: float) -> float | str:
        # JSON interoperable no representa de forma estándar NaN ni infinito.
        if math.isfinite(value):
            return value
        if math.isnan(value):
            return 'NaN'
        if value > 0:
            return 'Infinity'
        return '-Infinity'

    def _truncate(self, value: str) -> str:
        if len(value) <= self.max_string_length:
            return value
        # El sufijo permite distinguir un texto recortado de un texto completo con igual prefijo.
        return f'{value[: self.max_string_length]}...<truncated>'
