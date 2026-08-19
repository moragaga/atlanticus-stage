from __future__ import annotations


# Error base de esta capability. Las capas superiores deciden cómo observarlo o registrarlo.
class JsonError(Exception):
    pass


# Indica que el caller entregó un contrato que no puede representarse como JSON estricto.
class JsonValidationError(JsonError, ValueError):
    pass


# Diferencia los fallos de lectura del filesystem de un documento JSON corrupto.
class JsonReadError(JsonError, OSError):
    pass


# Agrupa fallos al confirmar una escritura durable en el filesystem.
class JsonWriteError(JsonError, OSError):
    pass


# El archivo existe, pero no cumple el contrato JSON que Atlanticus puede consumir.
class JsonCorruptionError(JsonError):
    pass


# write_once encontró una identidad ya comprometida con un contenido diferente.
class JsonConflictError(JsonWriteError):
    pass
