# Configuración productiva del process PI Web API.
# Separamos límites propios de la integración (paths/WebIDs/timeouts HTTP) de las
# políticas del process (lookback, window, concurrencia interpolated y point guard).

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
    max_recovery_lookback_seconds: int = 3600
    max_recovery_window_seconds: int = 3600
    interpolated_max_parallel_requests: int = 3
    max_data_points: int = 150_000

    def __post_init__(self) -> None:
        if self.max_recovery_window_seconds > self.max_recovery_lookback_seconds:
            raise PiWebApiProcessConfigurationError(
                'PI_WEB_API_MAX_RECOVERY_WINDOW_SECONDS must not exceed '
                'PI_WEB_API_MAX_RECOVERY_LOOKBACK_SECONDS'
            )
        if not 1 <= self.interpolated_max_parallel_requests <= 3:
            raise PiWebApiProcessConfigurationError(
                'PI_WEB_API_INTERPOLATED_MAX_PARALLEL_REQUESTS must be between 1 and 3'
            )

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
                max_recovery_lookback_seconds=_positive_int(
                    configuration,
                    'PI_WEB_API_MAX_RECOVERY_LOOKBACK_SECONDS',
                ),
                max_recovery_window_seconds=_positive_int(
                    configuration,
                    'PI_WEB_API_MAX_RECOVERY_WINDOW_SECONDS',
                ),
                interpolated_max_parallel_requests=_bounded_int(
                    configuration,
                    'PI_WEB_API_INTERPOLATED_MAX_PARALLEL_REQUESTS',
                    minimum=1,
                    maximum=3,
                ),
                max_data_points=_positive_int(
                    configuration,
                    'PI_WEB_API_MAX_DATA_POINTS',
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
        ConfigurationVariableSpec(key='PI_WEB_API_INTERPOLATED_MAX_WEB_IDS', default='200'),
        ConfigurationVariableSpec(key='PI_WEB_API_RECORDED_MAX_WEB_IDS', default='100'),
        ConfigurationVariableSpec(key='PI_WEB_API_MAX_RECOVERY_LOOKBACK_SECONDS', default='3600'),
        ConfigurationVariableSpec(key='PI_WEB_API_MAX_RECOVERY_WINDOW_SECONDS', default='3600'),
        ConfigurationVariableSpec(key='PI_WEB_API_INTERPOLATED_MAX_PARALLEL_REQUESTS', default='3'),
        ConfigurationVariableSpec(key='PI_WEB_API_MAX_DATA_POINTS', default='150000'),
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


def _bounded_int(
    configuration: ResolvedConfiguration,
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = _positive_int(configuration, key)
    if not minimum <= value <= maximum:
        raise PiWebApiProcessConfigurationError(f'{key} must be between {minimum} and {maximum}')
    return value


def _bool(configuration: ResolvedConfiguration, key: str) -> bool:
    value = configuration.get_bool(key)
    if value is None:
        raise PiWebApiProcessConfigurationError(f'{key} must contain a boolean value')
    return value
