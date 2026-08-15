# Modo temporal de validación local del lease.
# Usa el runtime oficial y la misma JobDefinition, pero no construye cliente PI ni toca WebIDs, datasets o watermarks.
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from ada.processes.pi_web_api.composition import PI_WEB_API_JOB_DEFINITION
from ada.processes.pi_web_api.errors import PiWebApiProcessConfigurationError
from atlanticus.configuration import (
    ConfigurationBootstrap,
    ConfigurationValueError,
    ConfigurationVariableSpec,
    ResolvedConfiguration,
)
from atlanticus.runtime import (
    JobRuntimeContext,
    RuntimeConfiguration,
    RuntimeExecutionResult,
    execute_job,
)

LEASE_SMOKE_MODE_VARIABLE = 'PI_WEB_API_LEASE_SMOKE_MODE'
_TRUE_VALUES = frozenset({'1', 'true', 'yes', 'on'})
_FALSE_VALUES = frozenset({'0', 'false', 'no', 'off'})


def lease_smoke_enabled(
    *,
    process_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> bool:
    source_values = os.environ if environ is None else environ
    root = Path(process_root)
    bootstrap = ConfigurationBootstrap.from_process(
        specs=(
            ConfigurationVariableSpec(
                key=LEASE_SMOKE_MODE_VARIABLE,
                default='false',
            ),
        ),
        process_values=source_values,
        dotenv_path=root / '.env',
    )
    if not bootstrap.environment.is_local:
        raw_value = source_values.get(LEASE_SMOKE_MODE_VARIABLE)
        if raw_value is None:
            return False
        enabled = _parse_boolean(raw_value)
        if enabled:
            raise PiWebApiProcessConfigurationError(
                'PI_WEB_API_LEASE_SMOKE_MODE is only available in local environment'
            )
        return False

    configuration = bootstrap.load(process_values=source_values)
    try:
        enabled = configuration.get_bool(LEASE_SMOKE_MODE_VARIABLE)
    except ConfigurationValueError as error:
        raise PiWebApiProcessConfigurationError(str(error)) from error
    return bool(enabled)


def load_lease_smoke_configuration(
    *,
    process_root: str | Path,
    environ: Mapping[str, str] | None = None,
) -> ResolvedConfiguration:
    source_values = os.environ if environ is None else environ
    root = Path(process_root)
    bootstrap = ConfigurationBootstrap.from_process(
        specs=_lease_smoke_specs(),
        process_values=source_values,
        dotenv_path=root / '.env',
    )
    if not bootstrap.environment.is_local:
        raise PiWebApiProcessConfigurationError(
            'Lease smoke validation is only available in local environment'
        )
    return bootstrap.load(process_values=source_values)


def run_lease_smoke(
    *,
    process_root: str | Path,
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimeExecutionResult:
    configuration = load_lease_smoke_configuration(
        process_root=process_root,
        environ=environ,
    )
    runtime_configuration = RuntimeConfiguration.from_sources(environ=configuration.values)
    lease_path = (
        runtime_configuration.runtime_root
        / 'leases'
        / f'{PI_WEB_API_JOB_DEFINITION.job_key}.json'
    )
    print('[lease-smoke] PI Web API is disabled for this execution.', flush=True)
    if lease_path.exists():
        wait_description = (
            'within the remaining safe runtime budget'
            if PI_WEB_API_JOB_DEFINITION.lease_wait_seconds is None
            else f'for up to {PI_WEB_API_JOB_DEFINITION.lease_wait_seconds:g} seconds'
        )
        print(
            '[lease-smoke] Existing lease detected; waiting for ownership '
            f'{wait_description}: {lease_path}',
            flush=True,
        )
    else:
        print(
            f'[lease-smoke] No existing lease detected; attempting ownership: {lease_path}',
            flush=True,
        )
    return execute_job(
        definition=PI_WEB_API_JOB_DEFINITION,
        iteration=_lease_smoke_iteration,
        argv=argv,
        environ=configuration.values,
    )


def _lease_smoke_iteration(context: JobRuntimeContext) -> None:
    if not isinstance(context, JobRuntimeContext):
        raise TypeError('context must be a JobRuntimeContext')
    print(
        '[lease-smoke] lease owned '
        f'pid={os.getpid()} run_id={context.run_id} iteration={context.iteration}',
        flush=True,
    )


def _lease_smoke_specs() -> tuple[ConfigurationVariableSpec, ...]:
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(
            key='ATLANTICUS_AZURE_OBSERVABILITY_MODE',
            default='off',
        ),
        ConfigurationVariableSpec(
            key='ATLANTICUS_AZURE_OBSERVABILITY_PROFILE',
            required=False,
        ),
        ConfigurationVariableSpec(
            key='APPLICATION_INSIGHTS_CONNECTION_STRING',
            required=False,
            sensitive=True,
        ),
    )


def _parse_boolean(value: str) -> bool:
    if not isinstance(value, str):
        raise TypeError(f'{LEASE_SMOKE_MODE_VARIABLE} must be a string')
    normalized = value.lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise PiWebApiProcessConfigurationError(
        f'{LEASE_SMOKE_MODE_VARIABLE} must contain a valid boolean value'
    )
