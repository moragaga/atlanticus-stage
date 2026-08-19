from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "scripts" / "distribute-processes.py"
)
SPEC = importlib.util.spec_from_file_location(
    "atlanticus_distribute_processes", SCRIPT_PATH
)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

DistributionError = MODULE.DistributionError
PROCESS_JOBS = MODULE.PROCESS_JOBS
distribute = MODULE.distribute
distribution_root = MODULE.distribution_root
select_jobs = MODULE.select_jobs


def test_job_catalog_is_stable_and_ordered() -> None:
    assert tuple((job.number, job.name) for job in PROCESS_JOBS) == (
        ("01", "pi-web-api"),
        ("02", "notpii"),
        ("03", "dispatch"),
        ("04", "blockgrade"),
        ("05", "fabrica"),
        ("06", "remanentes"),
    )


def test_selection_preserves_stable_job_numbers_and_catalog_order() -> None:
    jobs = select_jobs(("remanentes", "dispatch"), include_all=False)

    assert tuple((job.container_name, job.name) for job in jobs) == (
        ("job03", "dispatch"),
        ("job06", "remanentes"),
    )


def test_selection_requires_processes_or_all_and_rejects_unknown() -> None:
    with pytest.raises(DistributionError, match="select at least one process"):
        select_jobs((), include_all=False)
    with pytest.raises(DistributionError, match="unknown process selection: unknown"):
        select_jobs(("unknown",), include_all=False)
    with pytest.raises(DistributionError, match="cannot be combined with --all"):
        select_jobs(("dispatch",), include_all=True)


def test_distribution_name_is_safe_and_always_resolves_inside_atlanticus(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"

    assert (
        distribution_root(repository, "ada-web")
        == repository / "distribution" / "ada-web"
    )
    with pytest.raises(DistributionError, match="invalid distribution name"):
        distribution_root(repository, "../consumer")


def test_distribution_is_self_contained_and_generates_platform_services_and_local_compose(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _write_artifact(repository, "dispatch", cpus=3, memory="4g")
    _write_artifact(repository, "remanentes")
    jobs = select_jobs(("dispatch", "remanentes"), include_all=False)

    target = distribute(repository_root=repository, name="ada-web", jobs=jobs)

    assert target == repository / "distribution" / "ada-web"
    services_text = (target / "services.json").read_text(encoding="utf-8")
    services = json.loads(services_text)
    assert services_text.startswith('[\n  {\n    "repository"')
    assert services == [
        {
            "repository": "dispatch",
            "excecution_file": "dispatch",
            "container_name": "job03",
            "config_file": "processes/dispatch/config.json",
            "to_deploy": True,
            "to_stop": False,
            "to_working_hours_dev": False,
            "to_working_hours_uat": True,
        },
        {
            "repository": "remanentes",
            "excecution_file": "remanentes",
            "container_name": "job06",
            "config_file": "processes/remanentes/config.json",
            "to_deploy": True,
            "to_stop": False,
            "to_working_hours_dev": False,
            "to_working_hours_uat": True,
        },
    ]
    assert (target / "Dockerfile").read_text(encoding="utf-8") == "FROM scratch\n"
    assert (target / ".dockerignore").read_text(encoding="utf-8") == (
        "*\n!Dockerfile\n!processes/\n!processes/**\n"
    )
    assert (target / "processes/dispatch/config.detail.json").is_file()
    assert (target / "processes/dispatch/.env.detail").is_file()
    assert (target / "processes/remanentes/config.detail.json").is_file()
    assert not (target / "processes/pi-web-api").exists()
    assert (target / "scripts/local-process.sh").is_file()
    assert (target / "scripts/commented/local-process.sh").is_file()

    compose = (target / "local-deployment/compose.yaml").read_text(encoding="utf-8")
    assert "name: atlanticus-ada-web-local" in compose
    assert "image: atlanticus-ada-web-dispatch:local" in compose
    assert "image: atlanticus-ada-web-remanentes:local" in compose
    assert "context: .." in compose
    assert "FILENAME: dispatch" in compose
    assert "FILENAME: remanentes" in compose
    assert 'command: ["--run-once"]' in compose
    assert "../processes/dispatch/.env" in compose
    assert "../processes/remanentes/.env" in compose
    assert "cpus: 3" in compose
    assert "mem_limit: 4g" in compose
    assert "cpus: 0.5" in compose
    assert "mem_limit: 1g" in compose
    assert "runtime:/app/volume" in compose
    assert "pi-web-api:" not in compose

    bind_compose = (target / "local-deployment/compose.bind.yaml").read_text(
        encoding="utf-8"
    )
    assert "./runtime:/app/volume" in bind_compose
    assert "\nvolumes:\n  runtime:\n" not in bind_compose


def test_distribution_never_imports_artifact_env_or_artifact_config_json(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    artifact = _write_artifact(repository, "dispatch")
    (artifact / ".env").write_text("SECRET=artifact-local\n", encoding="utf-8")
    (artifact / ".env.test").write_text("SECRET=other\n", encoding="utf-8")
    (artifact / "config.json").write_text('{"owner":"artifact"}\n', encoding="utf-8")
    (artifact / "secrets.json").write_text('[{"owner":"artifact"}]\n', encoding="utf-8")

    target = distribute(
        repository_root=repository,
        name="consumer",
        jobs=select_jobs(("dispatch",), include_all=False),
    )

    process = target / "processes/dispatch"
    assert not (process / ".env").exists()
    assert not (process / ".env.test").exists()
    assert not (process / "config.json").exists()
    assert not (process / "secrets.json").exists()
    assert (process / ".env.detail").is_file()
    assert (process / "secrets.detail.json").is_file()


def test_redistribution_preserves_selected_consumer_config_and_local_env_but_is_exact(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    artifact = _write_artifact(repository, "dispatch")
    _write_artifact(repository, "remanentes")
    target = repository / "distribution/ada-web"
    existing_dispatch = target / "processes/dispatch"
    existing_dispatch.mkdir(parents=True)
    (existing_dispatch / "config.json").write_text(
        '{"memory":"1Gi"}\n', encoding="utf-8"
    )
    (existing_dispatch / "secrets.json").write_text(
        '[{"owner":"consumer"}]\n', encoding="utf-8"
    )
    (existing_dispatch / ".env").write_text("SECRET=consumer-local\n", encoding="utf-8")
    (existing_dispatch / "obsolete.txt").write_text("old\n", encoding="utf-8")
    existing_unselected = target / "processes/remanentes"
    existing_unselected.mkdir(parents=True)
    (existing_unselected / "config.json").write_text(
        '{"keep":true}\n', encoding="utf-8"
    )
    (target / "obsolete-root.txt").write_text("old\n", encoding="utf-8")
    (artifact / "src/catalog.py").write_text("REVISION = 2\n", encoding="utf-8")

    distributed = distribute(
        repository_root=repository,
        name="ada-web",
        jobs=select_jobs(("dispatch",), include_all=False),
    )

    process = distributed / "processes/dispatch"
    assert (process / "config.json").read_text(encoding="utf-8") == '{"memory":"1Gi"}\n'
    assert (process / "secrets.json").read_text(
        encoding="utf-8"
    ) == '[{"owner":"consumer"}]\n'
    assert (process / ".env").read_text(encoding="utf-8") == "SECRET=consumer-local\n"
    assert (process / "src/catalog.py").read_text(encoding="utf-8") == "REVISION = 2\n"
    assert not (process / "obsolete.txt").exists()
    assert not (distributed / "processes/remanentes").exists()
    assert not (distributed / "obsolete-root.txt").exists()


def test_distribution_validates_every_artifact_before_touching_existing_package(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _write_artifact(repository, "dispatch")
    incomplete = _write_artifact(repository, "remanentes")
    (incomplete / "config.detail.json").unlink()
    target = repository / "distribution/ada-web"
    target.mkdir(parents=True)
    original_services = target / "services.json"
    original_services.write_text('[{"existing":true}]\n', encoding="utf-8")

    with pytest.raises(DistributionError, match=r"incomplete \(config.detail\.json\)"):
        distribute(
            repository_root=repository,
            name="ada-web",
            jobs=select_jobs(("dispatch", "remanentes"), include_all=False),
        )

    assert original_services.read_text(encoding="utf-8") == '[{"existing":true}]\n'
    assert not (target / "processes").exists()


def test_generated_local_runner_validates_env_without_requiring_docker(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _write_artifact(repository, "dispatch")
    target = distribute(
        repository_root=repository,
        name="ada-web",
        jobs=select_jobs(("dispatch",), include_all=False),
    )
    runner = target / "scripts/local-process.sh"

    missing = subprocess.run(
        ["bash", str(runner), "validate"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing.returncode != 0
    assert "Configure each process .env" in missing.stderr

    (target / "processes/dispatch/.env").write_text("SECRET=local\n", encoding="utf-8")
    valid = subprocess.run(
        ["bash", str(runner), "validate"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
    )
    assert valid.returncode == 0


def test_commented_distributor_is_structurally_equivalent() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    production = ast.dump(
        ast.parse(
            (repository_root / "scripts/distribute-processes.py").read_text(
                encoding="utf-8"
            )
        ),
        include_attributes=False,
    )
    commented = ast.dump(
        ast.parse(
            (repository_root / "scripts/commented/distribute-processes.py").read_text(
                encoding="utf-8"
            )
        ),
        include_attributes=False,
    )

    assert production == commented


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    docker_root = repository / "deployment/processes"
    docker_root.mkdir(parents=True)
    (docker_root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (docker_root / ".dockerignore").write_text(
        "*\n!Dockerfile\n!processes/\n!processes/**\n", encoding="utf-8"
    )
    distribution_templates = repository / "deployment/distribution"
    commented_root = distribution_templates / "commented"
    commented_root.mkdir(parents=True)
    source_root = Path(__file__).resolve().parents[3] / "deployment/distribution"
    shutil.copy2(
        source_root / "local-process.sh", distribution_templates / "local-process.sh"
    )
    shutil.copy2(
        source_root / "commented/local-process.sh",
        commented_root / "local-process.sh",
    )
    return repository


def _write_artifact(
    repository: Path,
    name: str,
    *,
    cpus: float | None = None,
    memory: str | None = None,
) -> Path:
    root = repository / "artifacts/processes" / name
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
        'system-profile = "base"\n'
        f"{resources}",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "wheels").mkdir()
    (root / "src").mkdir()
    (root / "src/catalog.py").write_text("REVISION = 1\n", encoding="utf-8")
    (root / "config.detail.json").write_text('{"memory":"example"}\n', encoding="utf-8")
    (root / ".env.detail").write_text("REFERENCE=value\n", encoding="utf-8")
    (root / "secrets.detail.json").write_text("[]\n", encoding="utf-8")
    return root
