# Expone únicamente el constructor estable del catálogo; definitions.py queda
# como frontera declarativa.
from ada.processes.pi_web_api.catalog.provider import build_catalog

__all__ = ['build_catalog']
