from pathlib import Path
from types import SimpleNamespace

from ada.processes.pi_web_api.lease_smoke import (
    LEASE_SMOKE_MODE_VARIABLE,
    lease_smoke_enabled,
    load_lease_smoke_configuration,
    run_lease_smoke,
)
from atlanticus.runtime import JobRuntimeContext, RuntimeConfiguration


def _write_smoke_env(root: Path, volume_path: Path) -> None:
    (root / '.env').write_text(
        '\n'.join(
            (
                'ENVIRONMENT=local',
                'APPLICATION=ada',
                f'VOLUMEN_PATH={volume_path}',
                f'{LEASE_SMOKE_MODE_VARIABLE}=true',
            )
        )
        + '\n',
        encoding='utf-8',
    )


def test_lease_smoke_mode_can_be_enabled_from_dotenv_without_pi_values(tmp_path: Path) -> None:
    volume_path = tmp_path / 'volume'
    _write_smoke_env(tmp_path, volume_path)

    assert lease_smoke_enabled(process_root=tmp_path, environ={}) is True


def test_lease_smoke_configuration_requires_only_runtime_values(tmp_path: Path) -> None:
    volume_path = tmp_path / 'volume'
    _write_smoke_env(tmp_path, volume_path)

    configuration = load_lease_smoke_configuration(process_root=tmp_path, environ={})

    assert configuration.require('APPLICATION') == 'ada'
    assert configuration.require('VOLUMEN_PATH') == str(volume_path)
    assert configuration.get('PI_WEB_API_BASE_URL') is None
    assert configuration.get('PI_WEB_API_USERNAME') is None


def test_run_lease_smoke_uses_runtime_without_pi_composition(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    import ada.processes.pi_web_api.lease_smoke as lease_smoke_module

    volume_path = tmp_path / 'volume'
    _write_smoke_env(tmp_path, volume_path)
    sentinel = SimpleNamespace(name='runtime-result')
    captured = {}

    def fake_execute_job(*, definition, iteration, argv, environ):
        captured['definition'] = definition
        captured['argv'] = argv
        runtime_configuration = RuntimeConfiguration.from_sources(environ=environ)
        context = JobRuntimeContext.create(
            definition=definition,
            configuration=runtime_configuration,
            run_id='run-id',
            correlation_id='correlation-id',
        )
        context._begin_iteration(1)
        iteration(context)
        return sentinel

    monkeypatch.setattr(lease_smoke_module, 'execute_job', fake_execute_job)

    result = run_lease_smoke(
        process_root=tmp_path,
        argv=('--run-once',),
        environ={},
    )

    output = capsys.readouterr().out
    assert result is sentinel
    assert captured['argv'] == ('--run-once',)
    assert captured['definition'].lease_wait_seconds is None
    assert '[lease-smoke] PI Web API is disabled for this execution.' in output
    assert '[lease-smoke] lease owned' in output


def test_bootstrap_smoke_branch_does_not_load_pi_configuration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ada.processes.pi_web_api.bootstrap as bootstrap_module

    sentinel = SimpleNamespace(name='runtime-result')
    monkeypatch.setattr(bootstrap_module, 'lease_smoke_enabled', lambda **_: True)
    monkeypatch.setattr(bootstrap_module, 'run_lease_smoke', lambda **_: sentinel)

    def fail_load_configuration(**_):
        raise AssertionError('PI configuration must not be loaded in lease smoke mode')

    monkeypatch.setattr(bootstrap_module, 'load_configuration', fail_load_configuration)

    result = bootstrap_module.run(
        process_root=tmp_path,
        environ={
            'ENVIRONMENT': 'local',
            'APPLICATION': 'ada',
            'VOLUMEN_PATH': str(tmp_path / 'volume'),
        },
    )

    assert result is sentinel
