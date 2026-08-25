from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from ada.processes.kpis_timeseries_delivery.composition import build_composition
from ada.processes.kpis_timeseries_delivery.errors import (
    KpiTimeseriesDeliveryConfigurationError,
)
from ada.processes.kpis_timeseries_delivery.settings import configuration_specs
from atlanticus.configuration import ConfigurationBootstrap, ResolvedConfiguration, SecretsManifest
from atlanticus.connectivity.key_vault import (
    KeyVaultClient,
    KeyVaultConfigurationError,
    KeyVaultSettings,
)
from atlanticus.kernel import Environment
from atlanticus.runtime import RuntimeExecutionResult

_COMPANY_VARIABLE = 'COMPANY_ABREV'
_PRODUCT_VARIABLE = 'PRODUCT_ABREV'


def load_configuration(
    *,
    process_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> ResolvedConfiguration:
    source_values = os.environ if environ is None else environ
    root = Path(process_root)
    specs = configuration_specs()
    bootstrap = ConfigurationBootstrap.from_process(
        specs=specs,
        process_values=source_values,
        dotenv_path=root / '.env',
    )
    environment = bootstrap.environment
    if environment.is_local:
        configuration = bootstrap.load(process_values=source_values)
        return _require_absolute_volume_path(configuration)
    manifest = SecretsManifest.from_path(root / 'secrets.json')
    configured_keys = frozenset(spec.key for spec in specs)
    secret_entries = tuple(
        item
        for item in manifest.entries
        if item.var_name in configured_keys and item.exists_in_key_vault
    )
    if not secret_entries:
        configuration = ConfigurationBootstrap(
            environment=environment,
            specs=specs,
            secrets_manifest=manifest,
        ).load(process_values=source_values)
        return _require_absolute_volume_path(configuration)
    try:
        vault_settings = _key_vault_settings(
            environment=environment,
            manifest=manifest,
            process_values=source_values,
        )
    except KeyVaultConfigurationError as error:
        raise KpiTimeseriesDeliveryConfigurationError(str(error)) from error
    with KeyVaultClient(settings=vault_settings) as resolver:
        configuration = ConfigurationBootstrap(
            environment=environment,
            specs=specs,
            secrets_manifest=manifest,
            secret_resolver=resolver,
        ).load(process_values=source_values)
    return _require_absolute_volume_path(configuration)


def _key_vault_settings(
    *,
    environment: Environment,
    manifest: SecretsManifest,
    process_values: Mapping[str, str],
) -> KeyVaultSettings:
    values = {**manifest.static_values(), **process_values}
    return KeyVaultSettings(
        company_abrev=_required_bootstrap_value(values, _COMPANY_VARIABLE),
        environment=environment,
        product_abrev=_required_bootstrap_value(values, _PRODUCT_VARIABLE),
    )


def _required_bootstrap_value(values: Mapping[str, str], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise KpiTimeseriesDeliveryConfigurationError(f'{key} is required to resolve Key Vault')
    if value != value.strip():
        raise KpiTimeseriesDeliveryConfigurationError(
            f'{key} must not contain surrounding whitespace'
        )
    return value


def _require_absolute_volume_path(configuration: ResolvedConfiguration) -> ResolvedConfiguration:
    configured_path = Path(configuration.require('VOLUMEN_PATH')).expanduser()
    if not configured_path.is_absolute():
        raise KpiTimeseriesDeliveryConfigurationError('VOLUMEN_PATH must be an absolute path')
    return configuration


def run(
    *,
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    process_root: str | Path | None = None,
) -> RuntimeExecutionResult:
    root = Path.cwd() if process_root is None else Path(process_root)
    source_values = os.environ if environ is None else environ
    configuration = load_configuration(process_root=root, environ=source_values)
    return build_composition(configuration=configuration).execute(argv=argv)


def main() -> None:
    run()
