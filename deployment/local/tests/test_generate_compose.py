from __future__ import annotations

import ast
from pathlib import Path

import pytest
from generate_compose import (
    DEFAULT_CPUS,
    DEFAULT_MEMORY,
    LocalDeploymentError,
    discover_processes,
    prepare_workspace,
    validate_environment_files,
)


def test_discovery_uses_central_resource_defaults(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_process(repository, "dispatch", profile="sqlserver")

    definitions = discover_processes(repository)

    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.name == "dispatch"
    assert definition.command == "ada-dispatch"
    assert definition.system_profile == "sqlserver"
    assert definition.cpus == DEFAULT_CPUS
    assert definition.memory == DEFAULT_MEMORY


def test_discovery_accepts_process_resource_override(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_process(repository, "dispatch", profile="sqlserver", cpus=3.5, memory="4g")

    definition = discover_processes(repository)[0]

    assert definition.cpus == 3.5
    assert definition.memory == "4g"


def test_environment_file_is_required_by_convention(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_process(repository, "dispatch", profile="sqlserver", env=False)

    with pytest.raises(LocalDeploymentError, match=r"processes/dispatch/\.env"):
        validate_environment_files(discover_processes(repository))


def test_named_volume_workspace_uses_artifacts_and_does_not_copy_env(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _write_process(repository, "dispatch", profile="sqlserver")
    _write_process(repository, "remanentes", profile="base")
    _write_artifact(repository, "dispatch", profile="sqlserver")
    _write_artifact(repository, "remanentes", profile="base")
    workspace = repository / ".runtime" / "local-deployment"

    compose_path = prepare_workspace(
        repository_root=repository,
        workspace_root=workspace,
        definitions=discover_processes(repository),
        volume_mode="named",
    )

    compose = compose_path.read_text(encoding="utf-8")
    assert "name: atlanticus-local" in compose
    assert "FILENAME: dispatch" in compose
    assert "FILENAME: remanentes" in compose
    assert "../../scopes/ada/processes/dispatch/.env" in compose
    assert "runtime:/app/volume" in compose
    assert "volumes:\n  runtime:\n" in compose
    assert "cpus: 2" in compose
    assert "mem_limit: 2g" in compose
    assert "restart:" not in compose
    assert (workspace / "processes" / "dispatch" / "uv.lock").is_file()
    assert (workspace / "compose" / "dispatch.yaml").is_file()
    assert not tuple(workspace.rglob(".env"))


def test_bind_workspace_preserves_runtime_and_uses_bind_mount(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_process(repository, "remanentes", profile="base")
    _write_artifact(repository, "remanentes", profile="base")
    workspace = repository / ".runtime" / "local-deployment"
    runtime = workspace / "runtime"
    runtime.mkdir(parents=True)
    marker = runtime / "state.keep"
    marker.write_text("state", encoding="utf-8")

    compose_path = prepare_workspace(
        repository_root=repository,
        workspace_root=workspace,
        definitions=discover_processes(repository),
        volume_mode="bind",
    )

    compose = compose_path.read_text(encoding="utf-8")
    assert "./runtime:/app/volume" in compose
    assert "\nvolumes:\n" not in compose
    assert marker.read_text(encoding="utf-8") == "state"


def test_prepare_rejects_stale_artifact_container_contract(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_process(repository, "dispatch", profile="sqlserver")
    _write_artifact(repository, "dispatch", profile="base")

    with pytest.raises(
        LocalDeploymentError, match="differs between source and artifact"
    ):
        prepare_workspace(
            repository_root=repository,
            workspace_root=repository / ".runtime" / "local-deployment",
            definitions=discover_processes(repository),
            volume_mode="named",
        )


def test_commented_generator_is_structurally_equivalent() -> None:
    deployment_root = Path(__file__).resolve().parents[1]
    production = ast.dump(
        ast.parse(
            (deployment_root / "generate_compose.py").read_text(encoding="utf-8")
        ),
        include_attributes=False,
    )
    commented = ast.dump(
        ast.parse(
            (deployment_root / "commented" / "generate_compose.py").read_text(
                encoding="utf-8"
            )
        ),
        include_attributes=False,
    )

    assert production == commented


def test_local_shell_contract_is_small_and_reproducible() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    shell = (repository_root / "scripts" / "local-process.sh").read_text(
        encoding="utf-8"
    )

    assert "up [--bind]" in shell
    assert "docker compose -f" in shell
    assert "compose down --remove-orphans" in shell
    assert "compose build --no-cache" in shell
    assert "compose up -d" in shell
    assert 'compose run --rm "${process}" --run-once' in shell
    assert "--env " not in shell


def test_dockerignore_exposes_only_dockerfile_and_processes() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    dockerignore = (
        repository_root / "deployment" / "processes" / ".dockerignore"
    ).read_text(encoding="utf-8")

    assert dockerignore.splitlines() == [
        "*",
        "!Dockerfile",
        "!processes/",
        "!processes/**",
    ]


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    docker_root = repository / "deployment" / "processes"
    docker_root.mkdir(parents=True)
    (docker_root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (docker_root / ".dockerignore").write_text(
        "*\n!Dockerfile\n!processes/\n!processes/**\n", encoding="utf-8"
    )
    return repository


def _write_process(
    repository: Path,
    name: str,
    *,
    profile: str,
    cpus: float | None = None,
    memory: str | None = None,
    env: bool = True,
) -> None:
    root = repository / "scopes" / "ada" / "processes" / name
    root.mkdir(parents=True)
    resources = ""
    if cpus is not None or memory is not None:
        resource_lines = []
        if cpus is not None:
            resource_lines.append(f"cpus = {cpus}")
        if memory is not None:
            resource_lines.append(f'memory = "{memory}"')
        resources = (
            "\n[tool.atlanticus.container.resources]\n"
            + "\n".join(resource_lines)
            + "\n"
        )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "ada-{name}-process"\n'
        'version = "0.1.0"\n'
        'requires-python = "==3.14.2"\n\n'
        "[project.scripts]\n"
        f'ada-{name} = "sample:main"\n\n'
        "[tool.atlanticus.container]\n"
        f'command = "ada-{name}"\n'
        f'system-profile = "{profile}"\n'
        f"{resources}",
        encoding="utf-8",
    )
    if env:
        (root / ".env").write_text("SECRET=local-only\n", encoding="utf-8")


def _write_artifact(
    repository: Path,
    name: str,
    *,
    profile: str,
    cpus: float | None = None,
    memory: str | None = None,
) -> None:
    root = repository / "artifacts" / "processes" / name
    root.mkdir(parents=True)
    resources = ""
    if cpus is not None or memory is not None:
        resource_lines = []
        if cpus is not None:
            resource_lines.append(f"cpus = {cpus}")
        if memory is not None:
            resource_lines.append(f'memory = "{memory}"')
        resources = (
            "\n[tool.atlanticus.container.resources]\n"
            + "\n".join(resource_lines)
            + "\n"
        )
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "ada-{name}-process"\n'
        'version = "0.1.0"\n'
        'requires-python = "==3.14.2"\n\n'
        "[project.scripts]\n"
        f'ada-{name} = "sample:main"\n\n'
        "[tool.atlanticus.container]\n"
        f'command = "ada-{name}"\n'
        f'system-profile = "{profile}"\n'
        f"{resources}",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "wheels").mkdir()
    (root / "src").mkdir()
