"""Errores públicos y sanitizados del conector Redis."""


# Ningún error público debe propagar mensajes sensibles del SDK.
class RedisError(Exception):
    """Error base de ``atlanticus-redis``."""


class RedisConfigurationError(RedisError):
    """Indica una configuración ausente o inválida."""


class RedisClosedError(RedisError):
    """Indica que el cliente fue cerrado definitivamente."""


class RedisConnectionError(RedisError):
    """Representa un fallo de conexión sin exponer endpoint ni credenciales."""


class RedisAuthenticationError(RedisError):
    """Indica que Redis rechazó la credencial."""


class RedisAuthorizationError(RedisError):
    """Indica que la credencial no posee permisos para la operación."""


class RedisPoolExhaustedError(RedisError):
    """Indica que el pool alcanzó su límite de conexiones."""


class RedisOperationError(RedisError):
    """Representa un fallo remoto no clasificado sin filtrar detalles sensibles."""


class RedisResultLimitError(RedisError):
    """Indica que una operación multi-key excedió el límite configurado."""

    def __init__(self, *, max_keys: int) -> None:
        self.max_keys = max_keys
        super().__init__(f'Redis operation exceeds the limit of {max_keys} keys')
