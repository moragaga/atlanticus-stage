from __future__ import annotations

import argparse
import os
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CPUS = 2.0
DEFAULT_MEMORY = '2g'
DEFAULT_VOLUME_PATH = '/app/volume'
DEFAULT_WORKSPACE = Path('.runtime/local-deployment')
PROJECT_NAME = 'atlanticus-local'
RUNTIME_VOLUME_KEY = 'runtime'
PROCESS_NAME_PATTERN = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
MEMORY_PATTERN = re.compile(r'^[1-9][0-9]*(?:\.[0-9]+)?[bkmg]?$', re.IGNORECASE)
ALLOWED_SYSTEM_PROFILES = frozenset({'base', 'sqlserver'})


class LocalDeploymentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProcessDefinition:
    name: str
    source_root: Path
    env_file: Path
    artifact_root: Path
    command: str
    system_profile: str
    cpus: float
    memory: str


def discover_processes(repository_root: Path) -> tuple[ProcessDefinition, ...]:
    processes_root = repository_root / 'scopes' / 'ada' / 'processes'
    if not processes_root.is_dir():
        raise LocalDeploymentError(f'processes directory not found: {processes_root}')
    definitions: list[ProcessDefinition] = []
    for pyproject_path in sorted(processes_root.glob('*/pyproject.toml')):
        name = pyproject_path.parent.name
        metadata = _read_toml(pyproject_path)
        container = _container_metadata(metadata, pyproject_path)
        if container is None:
            continue
        if not PROCESS_NAME_PATTERN.fullmatch(name):
            raise LocalDeploymentError(f'invalid process directory name: {name}')
        command, system_profile, cpus, memory = container
        definitions.append(
            ProcessDefinition(
                name=name,
                source_root=pyproject_path.parent,
                env_file=pyproject_path.parent / '.env',
                artifact_root=repository_root / 'artifacts' / 'processes' / name,
                command=command,
                system_profile=system_profile,
                cpus=cpus,
                memory=memory,
            )
        )
    if not definitions:
        raise LocalDeploymentError(f'no local processes found: {processes_root}')
    return tuple(definitions)


def validate_environment_files(definitions: tuple[ProcessDefinition, ...]) -> None:
    missing = tuple(
        definition.env_file for definition in definitions if not definition.env_file.is_file()
    )
    if missing:
        rendered = '\n'.join(f'  - {path}' for path in missing)
        raise LocalDeploymentError(f'local process .env file not found:\n{rendered}')


def validate_artifacts(definitions: tuple[ProcessDefinition, ...]) -> None:
    for definition in definitions:
        artifact_root = definition.artifact_root
        for relative in ('pyproject.toml', 'uv.lock', 'wheels', 'src'):
            path = artifact_root / relative
            if not path.exists():
                raise LocalDeploymentError(
                    f'process transport artifact is incomplete ({relative}): {artifact_root}'
                )
        artifact_metadata = _read_toml(artifact_root / 'pyproject.toml')
        artifact_container = _container_metadata(
            artifact_metadata,
            artifact_root / 'pyproject.toml',
        )
        if artifact_container is None:
            raise LocalDeploymentError(f'container contract not found in artifact: {artifact_root}')
        actual = artifact_container[:4]
        expected = (
            definition.command,
            definition.system_profile,
            definition.cpus,
            definition.memory,
        )
        if actual != expected:
            raise LocalDeploymentError(
                f'container contract differs between source and artifact: {definition.name}'
            )


def prepare_workspace(
    *,
    repository_root: Path,
    workspace_root: Path,
    definitions: tuple[ProcessDefinition, ...],
    volume_mode: str,
) -> Path:
    if volume_mode not in {'named', 'bind'}:
        raise LocalDeploymentError(f'unsupported local volume mode: {volume_mode}')
    validate_environment_files(definitions)
    validate_artifacts(definitions)
    workspace_root.mkdir(parents=True, exist_ok=True)
    for generated in (
        workspace_root / 'Dockerfile',
        workspace_root / '.dockerignore',
        workspace_root / 'compose.yaml',
        workspace_root / 'compose',
        workspace_root / 'processes',
    ):
        if generated.is_dir():
            shutil.rmtree(generated)
        elif generated.exists():
            generated.unlink()
    if volume_mode == 'bind':
        (workspace_root / 'runtime').mkdir(parents=True, exist_ok=True)
    docker_source = repository_root / 'deployment' / 'processes'
    shutil.copy2(docker_source / 'Dockerfile', workspace_root / 'Dockerfile')
    shutil.copy2(docker_source / '.dockerignore', workspace_root / '.dockerignore')
    processes_root = workspace_root / 'processes'
    processes_root.mkdir()
    for definition in definitions:
        shutil.copytree(definition.artifact_root, processes_root / definition.name)
    compose_root = workspace_root / 'compose'
    compose_root.mkdir()
    for definition in definitions:
        (compose_root / f'{definition.name}.yaml').write_text(
            _render_fragment(definition, workspace_root=workspace_root, volume_mode=volume_mode),
            encoding='utf-8',
        )
    compose_path = workspace_root / 'compose.yaml'
    compose_path.write_text(
        _render_compose(definitions, workspace_root=workspace_root, volume_mode=volume_mode),
        encoding='utf-8',
    )
    return compose_path


def _container_metadata(
    metadata: dict[str, Any],
    pyproject_path: Path,
) -> tuple[str, str, float, str] | None:
    tool = metadata.get('tool')
    if not isinstance(tool, dict):
        return None
    atlanticus = tool.get('atlanticus')
    if not isinstance(atlanticus, dict):
        return None
    container = atlanticus.get('container')
    if not isinstance(container, dict):
        return None
    command = container.get('command')
    system_profile = container.get('system-profile')
    if not isinstance(command, str) or not command:
        raise LocalDeploymentError(f'container command is invalid: {pyproject_path}')
    if system_profile not in ALLOWED_SYSTEM_PROFILES:
        raise LocalDeploymentError(f'container system profile is invalid: {pyproject_path}')
    resources = container.get('resources', {})
    if not isinstance(resources, dict):
        raise LocalDeploymentError(f'container resources must be a table: {pyproject_path}')
    cpus = resources.get('cpus', DEFAULT_CPUS)
    memory = resources.get('memory', DEFAULT_MEMORY)
    if isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or cpus <= 0:
        raise LocalDeploymentError(f'container cpus must be greater than zero: {pyproject_path}')
    if not isinstance(memory, str) or not MEMORY_PATTERN.fullmatch(memory):
        raise LocalDeploymentError(f'container memory is invalid: {pyproject_path}')
    return command, system_profile, float(cpus), memory.lower()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding='utf-8'))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise LocalDeploymentError(f'could not read TOML file: {path}') from error


def _render_service(
    definition: ProcessDefinition,
    *,
    workspace_root: Path,
    volume_mode: str,
    indent: str,
) -> str:
    env_path = Path(os.path.relpath(definition.env_file, workspace_root)).as_posix()
    volume_source = RUNTIME_VOLUME_KEY if volume_mode == 'named' else './runtime'
    return '\n'.join(
        (
            f'{indent}{definition.name}:',
            f'{indent}  image: atlanticus-{definition.name}:local',
            f'{indent}  build:',
            f'{indent}    context: .',
            f'{indent}    dockerfile: Dockerfile',
            f'{indent}    args:',
            f'{indent}      FILENAME: {definition.name}',
            f'{indent}  env_file:',
            f'{indent}    - {env_path}',
            f'{indent}  environment:',
            f'{indent}    VOLUMEN_PATH: {DEFAULT_VOLUME_PATH}',
            f'{indent}  volumes:',
            f'{indent}    - {volume_source}:{DEFAULT_VOLUME_PATH}',
            f'{indent}  cpus: {definition.cpus:g}',
            f'{indent}  mem_limit: {definition.memory}',
        )
    )


def _render_fragment(
    definition: ProcessDefinition,
    *,
    workspace_root: Path,
    volume_mode: str,
) -> str:
    return 'services:\n' + _render_service(
        definition,
        workspace_root=workspace_root,
        volume_mode=volume_mode,
        indent='  ',
    ) + '\n'


def _render_compose(
    definitions: tuple[ProcessDefinition, ...],
    *,
    workspace_root: Path,
    volume_mode: str,
) -> str:
    services = '\n'.join(
        _render_service(
            definition,
            workspace_root=workspace_root,
            volume_mode=volume_mode,
            indent='  ',
        )
        for definition in definitions
    )
    suffix = f'\nvolumes:\n  {RUNTIME_VOLUME_KEY}:\n' if volume_mode == 'named' else ''
    return f'name: {PROJECT_NAME}\nservices:\n{services}\n{suffix}'


def _repository_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Prepare the Atlanticus local Compose workspace.')
    parser.add_argument('action', choices=('validate', 'prepare'))
    parser.add_argument(
        '--repository-root',
        type=Path,
        default=_repository_root_from_script(),
    )
    parser.add_argument('--workspace-root', type=Path)
    parser.add_argument('--volume-mode', choices=('named', 'bind'), default='named')
    return parser.parse_args()


def main() -> None:
    arguments = _parse_arguments()
    repository_root = arguments.repository_root.resolve()
    definitions = discover_processes(repository_root)
    validate_environment_files(definitions)
    if arguments.action == 'validate':
        return
    workspace_root = (
        arguments.workspace_root.resolve()
        if arguments.workspace_root is not None
        else repository_root / DEFAULT_WORKSPACE
    )
    compose_path = prepare_workspace(
        repository_root=repository_root,
        workspace_root=workspace_root,
        definitions=definitions,
        volume_mode=arguments.volume_mode,
    )
    print(compose_path)


if __name__ == '__main__':
    try:
        main()
    except LocalDeploymentError as error:
        raise SystemExit(str(error)) from error
