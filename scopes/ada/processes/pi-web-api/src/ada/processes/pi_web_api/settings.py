from __future__ import annotations

from dataclasses import dataclass

from ada.processes.pi_web_api.errors import PiWebApiProcessConfigurationError
from atlanticus.configuration import (
    ConfigurationValueError,
    ConfigurationVariableSpec,
    ResolvedConfiguration,
)
from atlanticus.connectivity.http import (
    HttpAuthMode,
    HttpConfigurationError,
    HttpSettings,
)
from atlanticus.integrations.pi.web_api import (
    PiWebApiConfigurationError,
    PiWebApiLimits,
    PiWebApiSettings,
)


@dataclass(frozen=True, slots=True)
class PiWebApiProcessSettings:
    pi_web_api: PiWebApiSettings
    max_recovery_seconds: int = 3600

    @classmethod
    def from_configuration(
        cls,
        configuration: ResolvedConfiguration,
    ) -> PiWebApiProcessSettings:
        if not isinstance(configuration, ResolvedConfiguration):
            raise PiWebApiProcessConfigurationError('configuration must be a ResolvedConfiguration')
        try:
            limits = PiWebApiLimits(
                points_max_paths=_positive_int(configuration, 'PI_WEB_API_POINTS_MAX_PATHS'),
                interpolated_max_web_ids=_positive_int(
                    configuration,
                    'PI_WEB_API_INTERPOLATED_MAX_WEB_IDS',
                ),
                recorded_max_web_ids=_positive_int(
                    configuration,
                    'PI_WEB_API_RECORDED_MAX_WEB_IDS',
                ),
            )
            http = HttpSettings(
                base_url=configuration.require('PI_WEB_API_BASE_URL'),
                auth_mode=HttpAuthMode.BASIC,
                username=configuration.require('PI_WEB_API_USERNAME'),
                password=configuration.require('PI_WEB_API_PASSWORD'),
                connect_timeout_seconds=_positive_int(
                    configuration,
                    'PI_WEB_API_CONNECT_TIMEOUT_SECONDS',
                ),
                read_timeout_seconds=_positive_int(
                    configuration,
                    'PI_WEB_API_READ_TIMEOUT_SECONDS',
                ),
                write_timeout_seconds=_positive_int(
                    configuration,
                    'PI_WEB_API_WRITE_TIMEOUT_SECONDS',
                ),
                pool_timeout_seconds=_positive_int(
                    configuration,
                    'PI_WEB_API_POOL_TIMEOUT_SECONDS',
                ),
                max_response_bytes=_positive_int(
                    configuration,
                    'PI_WEB_API_MAX_RESPONSE_BYTES',
                ),
                verify_tls=_bool(configuration, 'PI_WEB_API_VERIFY_TLS'),
                allow_insecure_http=_bool(
                    configuration,
                    'PI_WEB_API_ALLOW_INSECURE_HTTP',
                ),
            )
            return cls(
                pi_web_api=PiWebApiSettings(
                    pi_server=configuration.require('PI_WEB_API_SERVER'),
                    http=http,
                    limits=limits,
                ),
                max_recovery_seconds=_positive_int(
                    configuration,
                    'PI_WEB_API_MAX_RECOVERY_SECONDS',
                ),
            )
        except (
            ConfigurationValueError,
            HttpConfigurationError,
            PiWebApiConfigurationError,
        ) as error:
            raise PiWebApiProcessConfigurationError(str(error)) from error


def configuration_specs() -> tuple[ConfigurationVariableSpec, ...]:
    return (
        ConfigurationVariableSpec(key='APPLICATION'),
        ConfigurationVariableSpec(key='VOLUMEN_PATH'),
        ConfigurationVariableSpec(key='PI_WEB_API_BASE_URL'),
        ConfigurationVariableSpec(key='PI_WEB_API_SERVER'),
        ConfigurationVariableSpec(key='PI_WEB_API_USERNAME', sensitive=True),
        ConfigurationVariableSpec(key='PI_WEB_API_PASSWORD', sensitive=True),
        ConfigurationVariableSpec(key='PI_WEB_API_CONNECT_TIMEOUT_SECONDS', default='5'),
        ConfigurationVariableSpec(key='PI_WEB_API_READ_TIMEOUT_SECONDS', default='30'),
        ConfigurationVariableSpec(key='PI_WEB_API_WRITE_TIMEOUT_SECONDS', default='30'),
        ConfigurationVariableSpec(key='PI_WEB_API_POOL_TIMEOUT_SECONDS', default='5'),
        ConfigurationVariableSpec(key='PI_WEB_API_MAX_RESPONSE_BYTES', default='67108864'),
        ConfigurationVariableSpec(key='PI_WEB_API_VERIFY_TLS', default='true'),
        ConfigurationVariableSpec(key='PI_WEB_API_ALLOW_INSECURE_HTTP', default='false'),
        ConfigurationVariableSpec(key='PI_WEB_API_POINTS_MAX_PATHS', default='100'),
        ConfigurationVariableSpec(key='PI_WEB_API_INTERPOLATED_MAX_WEB_IDS', default='100'),
        ConfigurationVariableSpec(key='PI_WEB_API_RECORDED_MAX_WEB_IDS', default='100'),
        ConfigurationVariableSpec(key='PI_WEB_API_MAX_RECOVERY_SECONDS', default='3600'),
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


def _positive_int(configuration: ResolvedConfiguration, key: str) -> int:
    value = configuration.get_int(key)
    if value is None or value <= 0:
        raise PiWebApiProcessConfigurationError(f'{key} must be greater than zero')
    return value


def _bool(configuration: ResolvedConfiguration, key: str) -> bool:
    value = configuration.get_bool(key)
    if value is None:
        raise PiWebApiProcessConfigurationError(f'{key} must contain a boolean value')
    return value
