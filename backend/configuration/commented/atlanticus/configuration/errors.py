# Espejo pedagógico del módulo productivo de configuración.
# Conserva exactamente su comportamiento y agrega contexto para mantenimiento.
"""Errores públicos y sanitizados del bootstrap de configuración."""

from __future__ import annotations


class ConfigurationError(RuntimeError):
    """Error base para fallos de configuración Atlanticus."""


class ConfigurationSourceError(ConfigurationError):
    """La fuente requerida para el ambiente no está disponible."""


class SecretsManifestError(ConfigurationError, ValueError):
    """El manifiesto corporativo de secretos no cumple su contrato."""


class ConfigurationValueError(ConfigurationError, ValueError):
    """Una variable contiene un valor incompatible con el tipo solicitado."""


class MissingConfigurationVariablesError(ConfigurationError):
    """Una o más variables requeridas no pudieron resolverse."""

    def __init__(self, variable_names: tuple[str, ...]) -> None:
        self.variable_names = tuple(sorted(variable_names))
        variables = ', '.join(self.variable_names)
        super().__init__(f'Required environment variables are missing: {variables}.')


class SecretResolutionError(ConfigurationError):
    """Un secreto requerido no pudo resolverse de forma segura."""

    def __init__(self, variable_name: str) -> None:
        self.variable_name = variable_name
        super().__init__(
            f"Failed to resolve environment variable '{variable_name}' from Key Vault."
        )
