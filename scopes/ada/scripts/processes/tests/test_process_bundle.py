from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest
from scripts.processes import process_bundle
from scripts.processes.process_bundle import (
    ProcessBundleError,
    build_process_bundle,
    discover_projects,
    load_project,
    resolve_internal_dependencies,
)


def test_repository_root_matches_new_process_tooling_location() -> None:
    repository_root = process_bundle._repository_root_from_script()

    assert (
        repository_root / 'scopes' / 'ada' / 'scripts' / 'processes' / 'process_bundle.py'
    ).resolve() == Path(process_bundle.__file__).resolve()


def test_resolve_internal_dependencies_is_transitive_and_ordered(tmp_path: Path) -> None:
    repository_root = tmp_path / 'repository'
    process_root = repository_root / 'scopes' / 'ada' / 'processes' / 'sample'
    dependency_a = repository_root / 'backend' / 'dependency-a'
    dependency_b = repository_root / 'backend' / 'dependency-b'
    _write_project(
        process_root,
        name='ada-sample-process',
        dependencies=('atlanticus-dependency-a==0.1.0', 'pandas==3.0.3'),
        command='ada-sample',
        system_profile='base',
    )
    _write_project(
        dependency_a,
        name='atlanticus-dependency-a',
        dependencies=('atlanticus-dependency-b==0.1.0',),
    )
    _write_project(dependency_b, name='atlanticus-dependency-b')

    dependencies = resolve_internal_dependencies(
        load_project(process_root),
        discover_projects(repository_root),
    )

    assert tuple(item.name for item in dependencies) == (
        'atlanticus-dependency-b',
        'atlanticus-dependency-a',
    )


def test_resolve_internal_dependencies_rejects_non_exact_internal_version(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / 'repository'
    process_root = repository_root / 'scopes' / 'ada' / 'processes' / 'sample'
    dependency = repository_root / 'backend' / 'dependency'
    _write_project(
        process_root,
        name='ada-sample-process',
        dependencies=('atlanticus-dependency>=0.1.0',),
        command='ada-sample',
        system_profile='base',
    )
    _write_project(dependency, name='atlanticus-dependency')

    with pytest.raises(ProcessBundleError, match='must be pinned exactly'):
        resolve_internal_dependencies(
            load_project(process_root),
            discover_projects(repository_root),
        )


def test_build_process_bundle_publishes_runtime_only_transport_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / 'repository'
    process_root = repository_root / 'scopes' / 'ada' / 'processes' / 'sample'
    dependency = repository_root / 'backend' / 'dependency'
    _write_project(
        process_root,
        name='ada-sample-process',
        dependencies=('atlanticus-dependency==0.1.0',),
        command='ada-sample',
        system_profile='base',
    )
    _write_project(dependency, name='atlanticus-dependency')
    (process_root / '.python-version').write_text('3.14.2\n', encoding='utf-8')
    (process_root / '.env').write_text('SECRET=must-not-travel\n', encoding='utf-8')
    (process_root / '.env.detail').write_text('ENVIRONMENT=local\n', encoding='utf-8')
    (process_root / 'config.detail.json').write_text('{}\n', encoding='utf-8')
    (process_root / 'secrets.detail.json').write_text('[]\n', encoding='utf-8')
    (process_root / 'src' / 'ada' / 'sample').mkdir(parents=True)
    (process_root / 'src' / 'ada' / 'sample' / '__init__.py').write_text('', encoding='utf-8')
    (process_root / 'tests').mkdir()
    (process_root / 'tests' / 'test_sample.py').write_text(
        'def test_sample():\n    assert True\n', encoding='utf-8'
    )
    (process_root / 'commented').mkdir()
    (process_root / 'commented' / 'sample.py').write_text('value = 1\n', encoding='utf-8')
    (process_root / 'docs').mkdir()
    (process_root / 'docs' / 'contract.md').write_text('contract\n', encoding='utf-8')
    (process_root / 'scripts').mkdir()
    (process_root / 'scripts' / 'check.sh').write_text('exit 0\n', encoding='utf-8')
    (process_root / 'FIRST_STEP.txt').write_text('uv sync --frozen\n', encoding='utf-8')

    def fake_run(command: tuple[str, ...], *, cwd: Path) -> None:
        if command[:2] == ('uv', 'build'):
            project = load_project(Path(command[2]))
            output = Path(command[command.index('--out-dir') + 1])
            output.mkdir(parents=True, exist_ok=True)
            wheel_name = re.sub(r'[-.]+', '_', project.name)
            (output / f'{wheel_name}-{project.version}-py3-none-any.whl').write_bytes(b'wheel')
            return
        if command[:2] == ('uv', 'lock'):
            (cwd / 'uv.lock').write_text('version = 1\n', encoding='utf-8')
            return
        if command[:2] == ('uv', 'sync'):
            command_path = cwd / '.venv' / 'bin' / 'ada-sample'
            command_path.parent.mkdir(parents=True)
            command_path.write_text('', encoding='utf-8')
            return
        if command[:2] == ('uv', 'run'):
            (cwd / '.pytest_cache').mkdir(exist_ok=True)
            (cwd / '.ruff_cache').mkdir(exist_ok=True)
            cache = cwd / 'src' / 'ada' / 'sample' / '__pycache__'
            cache.mkdir(parents=True, exist_ok=True)
            (cache / 'sample.pyc').write_bytes(b'cache')
            return
        raise AssertionError(command)

    monkeypatch.setattr(process_bundle, '_run', fake_run)

    result = build_process_bundle(
        repository_root=repository_root,
        process_root=process_root,
        output_root=repository_root / 'artifacts' / 'processes',
    )

    assert (result / 'uv.lock').is_file()
    assert not (result / 'tests').exists()
    assert not (result / 'commented').exists()
    assert not (result / 'docs').exists()
    assert not (result / 'scripts').exists()
    assert (result / 'FIRST_STEP.txt').is_file()
    assert (result / '.env.detail').is_file()
    assert (result / 'config.detail.json').is_file()
    assert (result / 'secrets.detail.json').is_file()
    assert not (result / '.env').exists()
    assert not (result / '.venv').exists()
    assert not (result / '.pytest_cache').exists()
    assert not (result / '.ruff_cache').exists()
    assert not tuple(result.rglob('__pycache__'))
    assert not tuple(result.rglob('*.pyc'))
    assert sorted(path.name for path in (result / 'wheels').glob('*.whl')) == [
        'atlanticus_dependency-0.1.0-py3-none-any.whl'
    ]
    exported = tomllib.loads((result / 'pyproject.toml').read_text(encoding='utf-8'))
    assert exported['tool']['uv']['default-groups'] == []
    assert exported['tool']['uv']['sources']['atlanticus-dependency']['path'].startswith('wheels/')


def test_insert_export_uv_defaults_reuses_existing_tool_uv_section() -> None:
    source = '[project]\nname = "sample"\n\n[tool.uv]\nmanaged = true\n'

    exported = process_bundle._insert_export_uv_defaults(source)
    parsed = tomllib.loads(exported)

    assert parsed['tool']['uv']['managed'] is True
    assert parsed['tool']['uv']['default-groups'] == []


def test_insert_export_uv_defaults_rejects_process_override() -> None:
    source = '[tool.uv]\ndefault-groups = ["dev"]\n'

    with pytest.raises(ProcessBundleError, match='already declares tool.uv.default-groups'):
        process_bundle._insert_export_uv_defaults(source)


def test_process_tooling_gate_applies_safe_fixes_and_runs_tests() -> None:
    scripts_root = Path(__file__).resolve().parents[1]
    shell = (scripts_root / 'check.sh').read_text(encoding='utf-8')
    batch = (scripts_root / 'check.bat').read_text(encoding='utf-8')

    for script in (shell, batch):
        assert 'ruff check --fix --exit-zero' in script
        assert 'ruff format' in script
        assert 'python -m pytest -ra' in script
        assert 'Ruff lint' in script
        assert 'Pytest' in script


def test_commented_process_bundle_is_structurally_equivalent() -> None:
    scripts_root = Path(__file__).resolve().parents[1]
    production = ast.dump(
        ast.parse((scripts_root / 'process_bundle.py').read_text(encoding='utf-8')),
        include_attributes=False,
    )
    commented = ast.dump(
        ast.parse((scripts_root / 'commented' / 'process_bundle.py').read_text(encoding='utf-8')),
        include_attributes=False,
    )
    assert production == commented


def _write_project(
    project_root: Path,
    *,
    name: str,
    dependencies: tuple[str, ...] = (),
    command: str | None = None,
    system_profile: str | None = None,
) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    dependency_lines = ''.join(f'    "{item}",\n' for item in dependencies)
    script_section = ''
    container_section = ''
    if command is not None:
        script_section = f'\n[project.scripts]\n{command} = "sample:main"\n'
        container_section = (
            '\n[tool.atlanticus.container]\n'
            f'command = "{command}"\n'
            f'system-profile = "{system_profile}"\n'
        )
    (project_root / 'pyproject.toml').write_text(
        '[build-system]\n'
        'requires = ["setuptools==83.0.0"]\n'
        'build-backend = "setuptools.build_meta"\n\n'
        '[project]\n'
        f'name = "{name}"\n'
        'version = "0.1.0"\n'
        'requires-python = "==3.14.2"\n'
        'classifiers = ["Private :: Do Not Upload"]\n'
        'dependencies = [\n'
        f'{dependency_lines}'
        ']\n'
        f'{script_section}'
        '\n[dependency-groups]\n'
        'dev = ["pytest==9.1.1", "ruff==0.15.22"]\n'
        f'{container_section}',
        encoding='utf-8',
    )
