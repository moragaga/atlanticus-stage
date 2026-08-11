"""Definición declarativa y pequeña de un job backend."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from atlanticus.runtime.errors import RuntimeContractError


@dataclass(frozen=True, slots=True)
class JobDefinition:
    """Contrato estático del servicio y sus límites internos."""

    module_name: str
    service_name: str
    job_key: str | None = None
    run_once: bool = False
    sleep_seconds: float = 0.0
    iteration_timeout_seconds: float = 300.0
    execution_timeout_seconds: float = 330.0
    shutdown_grace_seconds: float = 15.0
    lease_timeout_seconds: float = 120.0
    lease_renew_seconds: float | None = None
    lease_wait_seconds: float = 15.0
    lease_poll_seconds: float = 1.0
    resource_sample_seconds: float = 1.0

    def __post_init__(self) -> None:
        _validate_module_name(self.module_name)
        _validate_path_identifier('service_name', self.service_name)
        job_key = self.service_name if self.job_key is None else self.job_key
        _validate_path_identifier('job_key', job_key)
        object.__setattr__(self, 'job_key', job_key)
        if not isinstance(self.run_once, bool):
            raise TypeError('run_once must be a bool')

        _validate_non_negative('sleep_seconds', self.sleep_seconds)
        _validate_positive('iteration_timeout_seconds', self.iteration_timeout_seconds)
        _validate_positive('execution_timeout_seconds', self.execution_timeout_seconds)
        _validate_positive('shutdown_grace_seconds', self.shutdown_grace_seconds)
        _validate_positive('lease_timeout_seconds', self.lease_timeout_seconds)
        lease_renew_seconds = (
            min(30.0, self.lease_timeout_seconds / 3)
            if self.lease_renew_seconds is None
            else self.lease_renew_seconds
        )
        _validate_positive('lease_renew_seconds', lease_renew_seconds)
        object.__setattr__(self, 'lease_renew_seconds', lease_renew_seconds)
        _validate_non_negative('lease_wait_seconds', self.lease_wait_seconds)
        _validate_positive('lease_poll_seconds', self.lease_poll_seconds)
        _validate_positive('resource_sample_seconds', self.resource_sample_seconds)

        if self.shutdown_grace_seconds >= self.execution_timeout_seconds:
            raise RuntimeContractError(
                'shutdown_grace_seconds must be lower than execution_timeout_seconds'
            )
        if self.iteration_timeout_seconds > self.execution_timeout_seconds:
            raise RuntimeContractError(
                'iteration_timeout_seconds must not exceed execution_timeout_seconds'
            )
        if lease_renew_seconds >= self.lease_timeout_seconds:
            raise RuntimeContractError(
                'lease_renew_seconds must be lower than lease_timeout_seconds'
            )

    @property
    def safe_execution_seconds(self) -> float:
        """Tiempo disponible antes de reservar la gracia de cierre."""

        return self.execution_timeout_seconds - self.shutdown_grace_seconds


def _validate_positive(name: str, value: float) -> None:
    _validate_number(name, value)
    if not math.isfinite(value) or value <= 0:
        raise RuntimeContractError(f'{name} must be a finite value greater than zero')


def _validate_non_negative(name: str, value: float) -> None:
    _validate_number(name, value)
    if not math.isfinite(value) or value < 0:
        raise RuntimeContractError(f'{name} must be a finite value greater than or equal to zero')


def _validate_number(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f'{name} must be an int or float')


def _validate_module_name(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError('module_name must be a string')
    if len(value) > 200 or not re.fullmatch(
        r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*',
        value,
    ):
        raise RuntimeContractError('module_name must be a valid dotted module identifier')


def _validate_path_identifier(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', value):
        raise RuntimeContractError(
            f'{name} must contain only letters, numbers, dots, underscores, or hyphens'
        )
