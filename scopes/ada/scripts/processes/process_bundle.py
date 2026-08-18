from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

PYTHON_VERSION = '3.14.2'
BUNDLE_DEPENDENCY_GROUP = 'bundle-internal'
TRANSPORT_EXCLUDED_ROOT_NAMES = frozenset({'commented', 'docs', 'scripts', 'tests'})
ALLOWED_SYSTEM_PROFILES = frozenset({'base', 'sqlserver'})
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        '.git',
        '.mypy_cache',
        '.pytest_cache',
        '.ruff_cache',
        '.venv',
        '__pycache__',
        'artifacts',
        'build',
        'dist',
    }
)
DEPENDENCY_NAME_PATTERN = re.compile(r'^\s*([A-Za-z0-9][A-Za-z0-9._-]*)')
PROJECT_COPY_IGNORE_PATTERNS = (
    '.git',
    '.mypy_cache',
    '.pytest_cache',
    '.ruff_cache',
    '.venv',
    '__pycache__',
    'artifacts',
    'build',
    'dist',
    '.env',
    '*.egg-info',
    '*.dist-info',
    '*.pyc',
)


class ProcessBundleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProjectDefinition:
    root: Path
    name: str
    version: str
    requires_python: str
    dependencies: tuple[str, ...]
    scripts: MappingProxyType[str, str]
    source: MappingProxyType[str, Any]


@dataclass(frozen=True, slots=True)
class ContainerDefinition:
    command: str
    system_profile: str


def canonicalize_package_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProcessBundleError('package name must be a non-empty string')
    return re.sub(r'[-_.]+', '-', value.strip()).lower()


def load_project(project_root: Path) -> ProjectDefinition:
    pyproject_path = project_root / 'pyproject.toml'
    if not pyproject_path.is_file():
        raise ProcessBundleError(f'pyproject.toml not found: {pyproject_path}')
    try:
        source = tomllib.loads(pyproject_path.read_text(encoding='utf-8'))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ProcessBundleError(f'could not read project metadata: {pyproject_path}') from error
    project = source.get('project')
    if not isinstance(project, dict):
        raise ProcessBundleError(f'project metadata not found: {pyproject_path}')
    name = project.get('name')
    version = project.get('version')
    requires_python = project.get('requires-python')
    dependencies = project.get('dependencies', [])
    scripts = project.get('scripts', {})
    if not isinstance(name, str) or not name:
        raise ProcessBundleError(f'project name is invalid: {pyproject_path}')
    if not isinstance(version, str) or not version:
        raise ProcessBundleError(f'project version is invalid: {pyproject_path}')
    if not isinstance(requires_python, str):
        raise ProcessBundleError(f'project Python requirement is invalid: {pyproject_path}')
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        raise ProcessBundleError(f'project dependencies are invalid: {pyproject_path}')
    if not isinstance(scripts, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in scripts.items()
    ):
        raise ProcessBundleError(f'project scripts are invalid: {pyproject_path}')
    return ProjectDefinition(
        root=project_root,
        name=name,
        version=version,
        requires_python=requires_python,
        dependencies=tuple(dependencies),
        scripts=MappingProxyType(dict(scripts)),
        source=MappingProxyType(source),
    )


def discover_projects(repository_root: Path) -> MappingProxyType[str, ProjectDefinition]:
    projects: dict[str, ProjectDefinition] = {}
    for pyproject_path in sorted(repository_root.rglob('pyproject.toml')):
        relative_parts = pyproject_path.relative_to(repository_root).parts
        if any(part in IGNORED_DIRECTORY_NAMES for part in relative_parts):
            continue
        try:
            source = tomllib.loads(pyproject_path.read_text(encoding='utf-8'))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ProcessBundleError(
                f'could not read project metadata: {pyproject_path}'
            ) from error
        if not isinstance(source.get('project'), dict):
            continue
        project = load_project(pyproject_path.parent)
        canonical_name = canonicalize_package_name(project.name)
        if canonical_name in projects:
            previous = projects[canonical_name]
            raise ProcessBundleError(
                f'duplicate project name {project.name}: {previous.root} and {project.root}'
            )
        projects[canonical_name] = project
    return MappingProxyType(projects)


def load_container_definition(project: ProjectDefinition) -> ContainerDefinition:
    tool = project.source.get('tool', {})
    if not isinstance(tool, dict):
        raise ProcessBundleError(f'container contract not found: {project.root}')
    atlanticus = tool.get('atlanticus', {})
    if not isinstance(atlanticus, dict):
        raise ProcessBundleError(f'container contract not found: {project.root}')
    container = atlanticus.get('container')
    if not isinstance(container, dict):
        raise ProcessBundleError(f'container contract not found: {project.root}')
    command = container.get('command')
    system_profile = container.get('system-profile')
    if not isinstance(command, str) or not command:
        raise ProcessBundleError(f'container command is invalid: {project.root}')
    if command not in project.scripts:
        raise ProcessBundleError(
            f'container command {command} is not declared in project scripts: {project.root}'
        )
    if system_profile not in ALLOWED_SYSTEM_PROFILES:
        allowed = ', '.join(sorted(ALLOWED_SYSTEM_PROFILES))
        raise ProcessBundleError(
            f'container system profile must be one of {allowed}: {project.root}'
        )
    return ContainerDefinition(command=command, system_profile=system_profile)


def resolve_internal_dependencies(
    process: ProjectDefinition,
    projects: MappingProxyType[str, ProjectDefinition],
) -> tuple[ProjectDefinition, ...]:
    ordered: list[ProjectDefinition] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(project: ProjectDefinition) -> None:
        canonical_project_name = canonicalize_package_name(project.name)
        if canonical_project_name in visited:
            return
        if canonical_project_name in visiting:
            raise ProcessBundleError(f'cyclic internal dependency detected: {project.name}')
        visiting.add(canonical_project_name)
        for requirement in project.dependencies:
            dependency_name = _extract_dependency_name(requirement)
            dependency = projects.get(dependency_name)
            if dependency is None:
                continue
            _validate_internal_requirement(requirement=requirement, dependency=dependency)
            visit(dependency)
        visiting.remove(canonical_project_name)
        visited.add(canonical_project_name)
        if canonical_project_name != canonicalize_package_name(process.name):
            ordered.append(project)

    visit(process)
    return tuple(ordered)


def discover_processes(repository_root: Path) -> tuple[Path, ...]:
    processes_root = repository_root / 'scopes' / 'ada' / 'processes'
    if not processes_root.is_dir():
        raise ProcessBundleError(f'processes directory not found: {processes_root}')
    process_roots: list[Path] = []
    for pyproject_path in sorted(processes_root.glob('*/pyproject.toml')):
        project = load_project(pyproject_path.parent)
        try:
            load_container_definition(project)
        except ProcessBundleError:
            continue
        process_roots.append(pyproject_path.parent)
    if not process_roots:
        raise ProcessBundleError(f'no exportable processes found: {processes_root}')
    return tuple(process_roots)


def resolve_process_root(repository_root: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        direct_candidate = (Path.cwd() / candidate).resolve()
        repository_candidate = (repository_root / candidate).resolve()
        named_candidate = (repository_root / 'scopes' / 'ada' / 'processes' / value).resolve()
        candidates = (direct_candidate, repository_candidate, named_candidate)
    else:
        candidates = (candidate.resolve(),)
    for item in candidates:
        if (item / 'pyproject.toml').is_file():
            return item
    raise ProcessBundleError(f'process project not found: {value}')


def build_process_bundle(
    *,
    repository_root: Path,
    process_root: Path,
    output_root: Path,
    validate_installation: bool = True,
) -> Path:
    process = load_project(process_root)
    _validate_python_contract(process)
    load_container_definition(process)
    projects = discover_projects(repository_root)
    dependencies = resolve_internal_dependencies(process, projects)
    for dependency in dependencies:
        _validate_python_contract(dependency)
    output_path = output_root / process_root.name
    output_root.mkdir(parents=True, exist_ok=True)
    temporary_parent = Path(tempfile.mkdtemp(prefix=f'.{process_root.name}-', dir=output_root))
    temporary_project = temporary_parent / process_root.name
    try:
        _copy_process_project(source=process_root, target=temporary_project)
        wheel_sources = _build_internal_wheels(
            repository_root=repository_root,
            dependencies=dependencies,
            wheel_directory=temporary_project / 'wheels',
        )
        _write_export_pyproject(
            source_path=process_root / 'pyproject.toml',
            target_path=temporary_project / 'pyproject.toml',
            dependencies=dependencies,
            wheel_sources=wheel_sources,
        )
        _run(
            (
                'uv',
                'lock',
                '--python',
                PYTHON_VERSION,
                '--refresh',
                '--no-cache',
            ),
            cwd=temporary_project,
        )
        if validate_installation:
            _run(
                (
                    'uv',
                    'sync',
                    '--frozen',
                    '--group',
                    'dev',
                    '--python',
                    PYTHON_VERSION,
                    '--no-cache',
                ),
                cwd=temporary_project,
            )
            _validate_installed_command(
                project_root=temporary_project,
                command=load_container_definition(process).command,
            )
            _validate_bundle_project(temporary_project)
            shutil.rmtree(temporary_project / '.venv', ignore_errors=True)
        _remove_generated_metadata(temporary_project)
        _prune_transport_tree(temporary_project)
        if output_path.exists():
            shutil.rmtree(output_path)
        temporary_project.replace(output_path)
        return output_path
    finally:
        shutil.rmtree(temporary_parent, ignore_errors=True)


def _extract_dependency_name(requirement: str) -> str:
    match = DEPENDENCY_NAME_PATTERN.match(requirement)
    if match is None:
        raise ProcessBundleError(f'invalid dependency requirement: {requirement}')
    return canonicalize_package_name(match.group(1))


def _validate_internal_requirement(
    *,
    requirement: str,
    dependency: ProjectDefinition,
) -> None:
    pattern = re.compile(
        rf'^\s*{re.escape(dependency.name)}\s*==\s*{re.escape(dependency.version)}\s*$',
        re.IGNORECASE,
    )
    if pattern.fullmatch(requirement) is None:
        raise ProcessBundleError(
            f'internal dependency must be pinned exactly as '
            f'{dependency.name}=={dependency.version}: {requirement}'
        )


def _validate_python_contract(project: ProjectDefinition) -> None:
    expected = f'=={PYTHON_VERSION}'
    if project.requires_python != expected:
        raise ProcessBundleError(f'{project.name} must declare requires-python = "{expected}"')


def _copy_process_project(*, source: Path, target: Path) -> None:
    _copy_project_tree(source=source, target=target)
    lock_path = target / 'uv.lock'
    if lock_path.exists():
        lock_path.unlink()
    wheels_path = target / 'wheels'
    if wheels_path.exists():
        shutil.rmtree(wheels_path)


def _remove_generated_metadata(project_root: Path) -> None:
    for name in ('.pytest_cache', '.ruff_cache', '__pycache__'):
        for path in tuple(project_root.rglob(name)):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
    for pattern in ('*.egg-info', '*.dist-info', '*.pyc'):
        for path in tuple(project_root.rglob(pattern)):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()


def _prune_transport_tree(project_root: Path) -> None:
    for name in TRANSPORT_EXCLUDED_ROOT_NAMES:
        path = project_root / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def _validate_bundle_project(project_root: Path) -> None:
    commands = (
        ('uv', 'run', '--python', PYTHON_VERSION, '--no-sync', 'ruff', 'check', '.'),
        ('uv', 'run', '--python', PYTHON_VERSION, '--no-sync', 'ruff', 'format', '--check', '.'),
        (
            'uv',
            'run',
            '--python',
            PYTHON_VERSION,
            '--no-sync',
            'python',
            '-m',
            'pytest',
            '-ra',
            'tests',
        ),
    )
    for command in commands:
        _run(command, cwd=project_root)
    commented = project_root / 'commented'
    if commented.is_dir():
        _run(
            (
                'uv',
                'run',
                '--python',
                PYTHON_VERSION,
                '--no-sync',
                'python',
                '-m',
                'compileall',
                '-q',
                'commented',
            ),
            cwd=project_root,
        )


def _copy_project_tree(*, source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(*PROJECT_COPY_IGNORE_PATTERNS),
    )


def _build_internal_wheels(
    *,
    repository_root: Path,
    dependencies: tuple[ProjectDefinition, ...],
    wheel_directory: Path,
) -> MappingProxyType[str, str]:
    wheel_directory.mkdir(parents=True, exist_ok=True)
    build_root = wheel_directory.parent / '.wheel-build'
    sources: dict[str, str] = {}
    try:
        for dependency in dependencies:
            package_root = build_root / canonicalize_package_name(dependency.name)
            package_source = package_root / 'source'
            package_output = package_root / 'dist'
            _copy_project_tree(source=dependency.root, target=package_source)
            _run(
                (
                    'uv',
                    'build',
                    str(package_source),
                    '--wheel',
                    '--out-dir',
                    str(package_output),
                    '--clear',
                    '--no-sources',
                    '--python',
                    PYTHON_VERSION,
                    '--refresh',
                    '--no-cache',
                ),
                cwd=repository_root,
            )
            wheels = tuple(package_output.glob('*.whl'))
            if len(wheels) != 1:
                raise ProcessBundleError(
                    f'exactly one wheel was expected for {dependency.name}, found {len(wheels)}'
                )
            destination = wheel_directory / wheels[0].name
            if destination.exists():
                raise ProcessBundleError(f'duplicate wheel filename: {destination.name}')
            shutil.move(str(wheels[0]), destination)
            sources[dependency.name] = f'wheels/{destination.name}'
    finally:
        shutil.rmtree(build_root, ignore_errors=True)
    return MappingProxyType(sources)


def _write_export_pyproject(
    *,
    source_path: Path,
    target_path: Path,
    dependencies: tuple[ProjectDefinition, ...],
    wheel_sources: MappingProxyType[str, str],
) -> None:
    source_text = source_path.read_text(encoding='utf-8').rstrip()
    source = tomllib.loads(source_text)
    dependency_groups = source.get('dependency-groups', {})
    if not isinstance(dependency_groups, dict):
        raise ProcessBundleError(f'dependency groups are invalid: {source_path}')
    if BUNDLE_DEPENDENCY_GROUP in dependency_groups:
        raise ProcessBundleError(
            f'process project already declares {BUNDLE_DEPENDENCY_GROUP}: {source_path}'
        )
    source_text = _remove_uv_sources_section(source_text)
    source_text = _insert_bundle_dependency_group(
        source_text=source_text,
        dependencies=dependencies,
    )
    source_text = _insert_export_uv_defaults(source_text)
    lines = [source_text.rstrip(), '', '[tool.uv.sources]']
    for package_name, wheel_path in sorted(wheel_sources.items()):
        lines.append(f'{package_name} = {{ path = "{wheel_path}" }}')
    target_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _remove_uv_sources_section(source_text: str) -> str:
    pattern = re.compile(
        r'(?ms)^\[tool\.uv\.sources\]\s*\n.*?(?=^\[|\Z)'
    )
    return pattern.sub('', source_text).rstrip()


def _insert_export_uv_defaults(source_text: str) -> str:
    section = re.search(r'(?m)^\[tool\.uv\]\s*$', source_text)
    if section is None:
        return f'{source_text.rstrip()}\n\n[tool.uv]\ndefault-groups = []'
    next_section = re.search(r'(?m)^\[', source_text[section.end() :])
    insert_at = len(source_text) if next_section is None else section.end() + next_section.start()
    prefix = source_text[: section.end()]
    body = source_text[section.end() : insert_at]
    suffix = source_text[insert_at:]
    if re.search(r'(?m)^\s*default-groups\s*=', body):
        raise ProcessBundleError('process project already declares tool.uv.default-groups')
    body = f'\ndefault-groups = []{body}'
    return f'{prefix}{body}{suffix}'


def _insert_bundle_dependency_group(
    *,
    source_text: str,
    dependencies: tuple[ProjectDefinition, ...],
) -> str:
    group_lines = [f'{BUNDLE_DEPENDENCY_GROUP} = [']
    group_lines.extend(
        f'    "{dependency.name}=={dependency.version}",' for dependency in dependencies
    )
    group_lines.append(']')
    group_text = '\n'.join(group_lines)
    section = re.search(r'(?m)^\[dependency-groups\]\s*$', source_text)
    if section is None:
        return f'{source_text.rstrip()}\n\n[dependency-groups]\n{group_text}'
    next_section = re.search(r'(?m)^\[', source_text[section.end() :])
    insert_at = len(source_text) if next_section is None else section.end() + next_section.start()
    prefix = source_text[:insert_at].rstrip()
    suffix = source_text[insert_at:].lstrip('\n')
    if not suffix:
        return f'{prefix}\n{group_text}'
    return f'{prefix}\n{group_text}\n\n{suffix}'


def _validate_installed_command(*, project_root: Path, command: str) -> None:
    if os.name == 'nt':
        candidates = (
            project_root / '.venv' / 'Scripts' / f'{command}.exe',
            project_root / '.venv' / 'Scripts' / f'{command}.cmd',
        )
    else:
        candidates = (project_root / '.venv' / 'bin' / command,)
    if not any(candidate.is_file() for candidate in candidates):
        raise ProcessBundleError(f'installed process command not found: {command}')


def _run(command: tuple[str, ...], *, cwd: Path) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProcessBundleError(f'command failed: {" ".join(command)}') from error


def _repository_root_from_script() -> Path:
    return Path(__file__).resolve().parents[4]


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Exporta procesos ADA como proyectos autónomos con wheels internos y uv.lock.'
    )
    parser.add_argument(
        'processes',
        nargs='*',
        help='Nombres o rutas de procesos. Sin valores exporta todos los procesos declarados.',
    )
    parser.add_argument(
        '--repository-root',
        type=Path,
        default=_repository_root_from_script(),
        help='Raíz del repositorio Atlanticus.',
    )
    parser.add_argument(
        '--output-root',
        type=Path,
        help='Directorio de salida. Por defecto usa artifacts/processes en la raíz.',
    )
    parser.add_argument(
        '--skip-install-validation',
        action='store_true',
        help='Genera el bundle sin ejecutar uv sync para validarlo.',
    )
    return parser.parse_args()


def main() -> None:
    arguments = _parse_arguments()
    repository_root = arguments.repository_root.resolve()
    output_root = (
        arguments.output_root.resolve()
        if arguments.output_root is not None
        else repository_root / 'artifacts' / 'processes'
    )
    process_roots = (
        tuple(resolve_process_root(repository_root, value) for value in arguments.processes)
        if arguments.processes
        else discover_processes(repository_root)
    )
    for process_root in process_roots:
        output_path = build_process_bundle(
            repository_root=repository_root,
            process_root=process_root,
            output_root=output_root,
            validate_installation=not arguments.skip_install_validation,
        )
        print(output_path)


if __name__ == '__main__':
    main()
