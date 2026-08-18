# Centraliza las tablas concretas que esta implementación de Blockgrade puede consumir.
from ada.processes.blockgrade.catalog.tables.mms_blockgrade_details_bucket import (
    DEFINITION as MMS_BLOCKGRADE_DETAILS_BUCKET,
)

# El provider conserva todas las definiciones y filtra solamente las que tienen enabled=True.
DEFINITIONS = (MMS_BLOCKGRADE_DETAILS_BUCKET,)
