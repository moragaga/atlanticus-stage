# Espejo pedagógico de bindings, routing y carga física de datos operacionales.
from __future__ import annotations

from enum import StrEnum


class PiSourceProvider(StrEnum):
    PI_WEB_API = 'pi_web_api'
    NOTPII = 'notpii'
