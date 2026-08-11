from __future__ import annotations

import pytest

from atlanticus.runtime import JobDefinition, RuntimeContractError


def test_job_definition_exposes_safe_execution_budget() -> None:
    definition = JobDefinition(
        module_name='dispatch_ingestion',
        service_name='dispatch-ingestion-job',
    )

    assert definition.module_name == 'dispatch_ingestion'
    assert definition.service_name == 'dispatch-ingestion-job'
    assert definition.safe_execution_seconds == 315


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('iteration_timeout_seconds', 331),
        ('shutdown_grace_seconds', 330),
        ('lease_renew_seconds', 350),
    ],
)
def test_job_definition_rejects_incoherent_time_contract(field, value) -> None:
    values = {
        'module_name': 'job',
        'service_name': 'job-service',
        field: value,
    }

    with pytest.raises(RuntimeContractError):
        JobDefinition(**values)


def test_job_definition_allows_renewable_lease_shorter_than_execution_window() -> None:
    definition = JobDefinition(
        module_name='dispatch',
        service_name='dispatch',
        job_key='dispatch-materialization',
        execution_timeout_seconds=86_400,
        iteration_timeout_seconds=300,
        lease_timeout_seconds=120,
        lease_renew_seconds=30,
    )

    assert definition.lease_timeout_seconds == 120
    assert definition.lease_renew_seconds == 30


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('module_name', 'job/module'),
        ('module_name', 'job..module'),
        ('module_name', 'job.2module'),
        ('service_name', 'job/service'),
        ('job_key', 'job_key/one'),
        ('service_name', ' job'),
    ],
)
def test_job_definition_rejects_identifiers_that_could_collide_in_paths(field, value) -> None:
    values = {'module_name': 'job', 'service_name': 'job-service', field: value}

    with pytest.raises(RuntimeContractError):
        JobDefinition(**values)


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('run_once', 1),
        ('sleep_seconds', True),
        ('lease_wait_seconds', float('nan')),
        ('resource_sample_seconds', float('inf')),
    ],
)
def test_job_definition_rejects_invalid_direct_types(field, value) -> None:
    values = {'module_name': 'job', 'service_name': 'job-service', field: value}

    with pytest.raises((TypeError, ValueError, RuntimeContractError)):
        JobDefinition(**values)
