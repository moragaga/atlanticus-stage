# El proveedor PI es una decisión de infraestructura de Sources, no parte de la semántica de KpiSource.
# El proceso KPI resolverá PI_SOURCE al iniciar y entregará este enum a la composición del registry.
from __future__ import annotations

from enum import StrEnum


class PiSourceProvider(StrEnum):
    PI_WEB_API = 'pi_web_api'
    NOTPII = 'notpii'
