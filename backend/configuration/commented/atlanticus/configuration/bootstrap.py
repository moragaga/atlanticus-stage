# Espejo pedagógico del módulo productivo de configuración.
# Conserva exactamente su comportamiento y agrega contexto para mantenimiento.
"""Composición atómica de configuración local o desplegada."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from atlanticus.configuration.contracts import SecretResolver
from atlanticus.configuration.errors import (
    ConfigurationSourceError,
    MissingConfigurationVariablesError,
    SecretResolutionError,
)
from atlanticus.configuration.manifest import SecretsManifest
from atlanticus.configuration.models import (
    ConfigurationSource,
    ConfigurationVariableSpec,
    ResolvedConfiguration,
    normalize_configuration_value,
)
from atlanticus.kernel import ENVIRONMENT_VARIABLE, Environment, InvalidEnvironmentError


# Mantiene decisiones de composición, pero no modifica el ambiente global del intérprete.
@dataclass(frozen=True, slots=True)
class ConfigurationBootstrap:
    """Resuelve toda la configuración antes de iniciar un proceso."""

    environment: Environment
    specs: tuple[ConfigurationVariableSpec, ...]
    dotenv_path: Path = Path('.env')
    secrets_manifest: SecretsManifest | None = None
    secret_resolver: SecretResolver | None = None

    def __post_init__(self) -> None:
        # La anotación no valida en runtime; se bloquea una instancia parcialmente inválida.
        if not isinstance(self.environment, Environment):
            raise ConfigurationSourceError(
                'Configuration bootstrap environment must be an Environment instance.'
            )
        try:
            specs = tuple(self.specs)
        except TypeError:
            raise ConfigurationSourceError('Configuration specs must be iterable.') from None
        if any(not isinstance(spec, ConfigurationVariableSpec) for spec in specs):
            raise ConfigurationSourceError(
                'Configuration specs must contain ConfigurationVariableSpec instances.'
            )
        keys = [spec.key for spec in specs]
        # ENVIRONMENT se resuelve una sola vez y no forma parte del catálogo del consumidor.
        if ENVIRONMENT_VARIABLE in keys:
            raise ConfigurationSourceError(
                f"Configuration specs cannot declare reserved variable '{ENVIRONMENT_VARIABLE}'."
            )
        duplicate_keys = sorted({key for key in keys if keys.count(key) > 1})
        if duplicate_keys:
            duplicates = ', '.join(duplicate_keys)
            raise ConfigurationSourceError(
                f'Configuration specs contain duplicate variables: {duplicates}.'
            )
        if not isinstance(self.dotenv_path, str | Path):
            raise ConfigurationSourceError('dotenv_path must be a string or Path.')
        if self.secrets_manifest is not None and not isinstance(
            self.secrets_manifest, SecretsManifest
        ):
            raise ConfigurationSourceError(
                'secrets_manifest must be a SecretsManifest instance or None.'
            )
        if self.secret_resolver is not None and not callable(
            getattr(self.secret_resolver, 'get_secret', None)
        ):
            raise ConfigurationSourceError(
                'secret_resolver must provide a callable get_secret method.'
            )
        object.__setattr__(self, 'specs', specs)
        object.__setattr__(self, 'dotenv_path', Path(self.dotenv_path))

    @classmethod
    def from_process(
        cls,
        *,
        specs: Sequence[ConfigurationVariableSpec],
        process_values: Mapping[str, str] | None = None,
        dotenv_path: str | Path = '.env',
        secrets_manifest: SecretsManifest | None = None,
        secret_resolver: SecretResolver | None = None,
    ) -> ConfigurationBootstrap:
        """Valida ``ENVIRONMENT`` antes de seleccionar cualquier fuente."""

        # ENVIRONMENT se valida antes de mirar cualquier archivo para impedir un fallback inseguro.
        effective_values = os.environ if process_values is None else process_values
        if not isinstance(effective_values, Mapping):
            raise ConfigurationSourceError('Process configuration values must be a mapping.')
        # El proceso manda si declara ENVIRONMENT; solo su ausencia permite consultar .env local.
        environment, _ = _resolve_environment(
            effective_values,
            dotenv_path=dotenv_path,
        )
        return cls(
            environment=environment,
            specs=specs,
            dotenv_path=dotenv_path,
            secrets_manifest=secrets_manifest,
            secret_resolver=secret_resolver,
        )

    def load(
        self,
        *,
        process_values: Mapping[str, str] | None = None,
    ) -> ResolvedConfiguration:
        """Entrega una configuración completa o falla sin resultado parcial."""

        raw_process_values = os.environ if process_values is None else process_values
        if not isinstance(raw_process_values, Mapping):
            raise ConfigurationSourceError('Process configuration values must be a mapping.')
        effective_environment, environment_source = _resolve_environment(
            raw_process_values,
            dotenv_path=self.dotenv_path,
        )
        # Se impide que una instancia local cambie a desplegada, o viceversa, después de construirse.
        if effective_environment != self.environment:
            raise ConfigurationSourceError(
                f"Process '{ENVIRONMENT_VARIABLE}' does not match the bootstrap environment."
            )
        effective_process_values = dict(raw_process_values)
        # La política depende del ambiente validado, no de la existencia de .env o secrets.json.
        if self.environment.is_local:
            candidates, candidate_sources = self._load_local(effective_process_values)
        else:
            candidates, candidate_sources = self._load_deployed()
        return self._resolve_specs(
            candidates,
            candidate_sources,
            environment_source=environment_source,
        )

    def _load_local(
        self,
        process_values: Mapping[str, Any],
    ) -> tuple[dict[str, object], dict[str, ConfigurationSource]]:
        dotenv_data: Mapping[str, object] = {}
        try:
            if self.dotenv_path.is_file():
                dotenv_data = dotenv_values(self.dotenv_path, interpolate=False)
        except OSError, UnicodeError:
            # El error público identifica la fuente, pero no expone detalles físicos del sistema.
            raise ConfigurationSourceError(
                f'Local configuration file could not be read: {self.dotenv_path}.'
            ) from None

        # .env forma la base local y el ambiente del proceso prevalece, incluso cuando está vacío.
        candidates: dict[str, object] = dict(dotenv_data)
        sources = {
            key: ConfigurationSource.DOTENV
            for key, value in dotenv_data.items()
            if normalize_configuration_value(value, variable_name=key) is not None
        }
        for key, value in process_values.items():
            candidates[key] = value
            sources[key] = ConfigurationSource.PROCESS
        return candidates, sources

    def _load_deployed(
        self,
    ) -> tuple[dict[str, object], dict[str, ConfigurationSource]]:
        if self.secrets_manifest is None:
            raise ConfigurationSourceError(
                f'A secrets manifest is required for environment {self.environment}.'
            )

        # Antes de abrir una frontera remota se comprueba que el manifiesto pueda completar
        # estructuralmente todas las variables obligatorias que no tengan default.
        missing = tuple(
            spec.key
            for spec in self.specs
            if spec.required
            and spec.default is None
            and self.secrets_manifest.find(spec.key) is None
        )
        if missing:
            raise MissingConfigurationVariablesError(missing)

        candidates: dict[str, object] = {}
        sources: dict[str, ConfigurationSource] = {}
        # Se recorre el contrato del consumidor, no todo el manifiesto, para evitar operaciones
        # sobre secretos que este proceso no necesita.
        for spec in self.specs:
            entry = self.secrets_manifest.find(spec.key)
            if entry is None:
                continue
            if entry.exists_in_key_vault:
                if self.secret_resolver is None:
                    raise ConfigurationSourceError(
                        f"A secret resolver is required for environment variable '{entry.var_name}'."
                    )
                try:
                    value = self.secret_resolver.get_secret(entry.secret_name or '')
                    validated_value = normalize_configuration_value(
                        value,
                        variable_name=entry.var_name,
                    )
                except Exception:
                    # Se elimina la causa del SDK para evitar propagar URLs o detalles sensibles.
                    raise SecretResolutionError(entry.var_name) from None
                if validated_value is None:
                    raise SecretResolutionError(entry.var_name)
                candidates[entry.var_name] = validated_value
                sources[entry.var_name] = ConfigurationSource.KEY_VAULT
                continue

            candidates[entry.var_name] = entry.value
            sources[entry.var_name] = ConfigurationSource.MANIFEST

        return candidates, sources

    def _resolve_specs(
        self,
        candidates: Mapping[str, object],
        candidate_sources: Mapping[str, ConfigurationSource],
        *,
        environment_source: ConfigurationSource,
    ) -> ResolvedConfiguration:
        values = {ENVIRONMENT_VARIABLE: str(self.environment)}
        sources = {ENVIRONMENT_VARIABLE: environment_source}
        sensitive_keys: set[str] = set()
        missing: list[str] = []

        for spec in self.specs:
            value = normalize_configuration_value(candidates.get(spec.key), variable_name=spec.key)
            source = candidate_sources.get(spec.key)
            if value is None and spec.default is not None:
                value = spec.default
                source = ConfigurationSource.DEFAULT
            if value is None:
                if spec.required:
                    missing.append(spec.key)
                continue
            values[spec.key] = value
            sources[spec.key] = source or ConfigurationSource.PROCESS
            # Un valor proveniente de Key Vault es sensible por origen aunque el consumidor omita el flag.
            if spec.sensitive or source == ConfigurationSource.KEY_VAULT:
                sensitive_keys.add(spec.key)

        if missing:
            # Las ausencias se reportan juntas para corregir la configuración en una sola iteración.
            raise MissingConfigurationVariablesError(tuple(missing))

        return ResolvedConfiguration(
            environment=self.environment,
            values=values,
            sources=sources,
            sensitive_keys=frozenset(sensitive_keys),
        )


# ENVIRONMENT es una decisión previa a las demás fuentes. En despliegue siempre debe venir
# inyectado al proceso; .env solo puede declarar el ambiente especial local.
def _resolve_environment(
    process_values: Mapping[str, object],
    *,
    dotenv_path: str | Path,
) -> tuple[Environment, ConfigurationSource]:
    if ENVIRONMENT_VARIABLE in process_values:
        try:
            return Environment.from_mapping(process_values), ConfigurationSource.PROCESS
        except InvalidEnvironmentError:
            raise ConfigurationSourceError(
                f"Process configuration must define a valid '{ENVIRONMENT_VARIABLE}' value."
            ) from None

    try:
        local_path = Path(dotenv_path)
    except TypeError:
        raise ConfigurationSourceError('dotenv_path must be a string or Path.') from None
    try:
        if not local_path.is_file():
            raise ConfigurationSourceError(
                f"Process configuration must define '{ENVIRONMENT_VARIABLE}', "
                'or local configuration must declare it in .env.'
            )
        # No se interpola el archivo porque aquí necesitamos una fuente literal y auditable.
        dotenv_data = dotenv_values(local_path, interpolate=False)
    except ConfigurationSourceError:
        raise
    except OSError, UnicodeError:
        raise ConfigurationSourceError(
            f'Local configuration file could not be read: {local_path}.'
        ) from None

    try:
        environment = Environment.from_mapping(dotenv_data)
    except InvalidEnvironmentError:
        raise ConfigurationSourceError(
            f"Local configuration must define a valid '{ENVIRONMENT_VARIABLE}' value."
        ) from None
    # Un .env jamás selecciona un ambiente Azure; eso requiere inyección explícita del proceso.
    if not environment.is_local:
        raise ConfigurationSourceError(
            f"Deployed environment '{environment}' must define '{ENVIRONMENT_VARIABLE}' "
            'in process configuration.'
        )
    return environment, ConfigurationSource.DOTENV
