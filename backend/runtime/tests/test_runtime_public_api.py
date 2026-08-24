from __future__ import annotations

import pytest

import atlanticus.runtime as runtime
from atlanticus.kernel import OperationStatus


def test_public_api_contains_only_process_contracts_and_controlled_errors() -> None:
    assert set(runtime.__all__) == {
        'AtlanticusRuntimeError',
        'ConcurrentExecutionError',
        'JobDefinition',
        'JobRuntimeContext',
        'LeaseOwnershipLostError',
        'LeaseRenewalError',
        'RuntimeCancellationRequested',
        'RuntimeConfiguration',
        'RuntimeConfigurationError',
        'RuntimeContractError',
        'RuntimeExecutionResult',
        '__version__',
        'execute_job',
    }
    assert not hasattr(runtime, 'ExecutionLease')
    assert not hasattr(runtime, 'RuntimeOptions')
    assert not hasattr(runtime, 'ResourceMonitor')


@pytest.mark.parametrize(
    ('field', 'value'),
    [
        ('run_id', 'not-a-uuid'),
        ('correlation_id', 'not-a-uuid'),
        ('status', 'success'),
        ('iteration_count', True),
        ('duration_seconds', float('nan')),
        ('stop_reason', 'Not valid'),
    ],
)
def test_execution_result_rejects_invalid_direct_contract(field, value) -> None:
    values = {
        'run_id': '4af45e0b-bdde-4125-96f2-89aa7452dd64',
        'correlation_id': 'c5150d54-1f4f-4d62-b52c-0bc3eaeb1191',
        'status': OperationStatus.SUCCESS,
        'iteration_count': 1,
        'duration_seconds': 1.0,
        'stop_reason': 'completed',
        field: value,
    }

    with pytest.raises((TypeError, ValueError)):
        runtime.RuntimeExecutionResult(**values)


def test_runtime_version_exposes_adaptive_iteration_delay_release() -> None:
    assert runtime.__version__ == '0.6.0'
