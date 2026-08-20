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


def test_discovery_uses_artifact_and_central_resource_defaults(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_artifact(repository, "dispatch", profile="sqlserver")

    definitions = discover_processes(repository)

    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.name == "dispatch"
    assert definition.command == "ada-dispatch"
    assert definition.system_profile == "sqlserver"
    assert definition.cpus == DEFAULT_CPUS
    assert definition.memory == DEFAULT_MEMORY
    assert definition.env_file == repository / "artifacts/processes/dispatch/.env"


def test_discovery_accepts_artifact_resource_override(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_artifact(repository, "dispatch", profile="sqlserver", cpus=3.5, memory="4g")

    definition = discover_processes(repository)[0]

    assert definition.cpus == 3.5
    assert definition.memory == "4g"


def test_environment_file_is_required_in_artifact_by_convention(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_artifact(repository, "dispatch", profile="sqlserver", env=False)

    with pytest.raises(
        LocalDeploymentError, match=r"artifacts/processes/dispatch/\.env"
    ):
        validate_environment_files(discover_processes(repository))


def test_discovery_does_not_require_process_source(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _write_artifact(repository, "remanentes", profile="base")

    definitions = discover_processes(repository)

    assert tuple(definition.name for definition in definitions) == ("remanentes",)
    assert not (repository / "scopes").exists()


def test_named_volume_workspace_uses_customized_artifacts_and_does_not_copy_env(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    dispatch = _write_artifact(repository, "dispatch", profile="sqlserver")
    _write_artifact(repository, "remanentes", profile="base")
    customized_catalog = dispatch / "src" / "catalog.py"
    customized_catalog.write_text("CUSTOMIZED = True\n", encoding="utf-8")
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
    assert "../../artifacts/processes/dispatch/.env" in compose
    assert 'command: ["--run-once"]' in compose
    assert "runtime:/app/volume" in compose
    assert "volumes:\n  runtime:\n" in compose
    assert "cpus: 0.5" in compose
    assert "mem_limit: 1g" in compose
    assert "restart:" not in compose
    assert (workspace / "processes" / "dispatch" / "uv.lock").is_file()
    assert (workspace / "compose" / "dispatch.yaml").is_file()
    assert (workspace / "processes" / "dispatch" / "src" / "catalog.py").read_text(
        encoding="utf-8"
    ) == "CUSTOMIZED = True\n"
    assert not tuple(workspace.rglob(".env"))


def test_bind_workspace_preserves_runtime_and_uses_bind_mount(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
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


def test_workspace_rejects_incomplete_artifact(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    artifact = _write_artifact(repository, "dispatch", profile="sqlserver")
    (artifact / "uv.lock").unlink()

    with pytest.raises(LocalDeploymentError, match=r"incomplete \(uv.lock\)"):
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


def test_local_shell_separates_prepare_from_up_and_runs_e2e_once() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    shell = (repository_root / "scripts" / "local-process.sh").read_text(
        encoding="utf-8"
    )

    assert "prepare [--all|PROCESS [PROCESS ...]]|up [--bind]" in shell
    assert "command_prepare()" in shell
    assert "command_up()" in shell
    assert "compose down --remove-orphans" in shell
    assert "compose build --no-cache" in shell
    assert "compose up -d" in shell
    assert "compose ps -a" in shell
    assert 'compose run --rm "${process}" --run-once' in shell
    assert "Configure each artifact .env" in shell
    up_body = shell.split("command_up() {", 1)[1].split("command_down() {", 1)[0]
    assert "BUNDLER" not in up_body
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


def _write_artifact(
    repository: Path,
    name: str,
    *,
    profile: str,
    cpus: float | None = None,
    memory: str | None = None,
    env: bool = True,
) -> Path:
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
    if env:
        (root / ".env").write_text("SECRET=local-only\n", encoding="utf-8")
    return root
