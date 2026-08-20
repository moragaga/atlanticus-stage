#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tomllib

PROCESS_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DISTRIBUTION_NAME_PATTERN = PROCESS_NAME_PATTERN
MEMORY_PATTERN = re.compile(r"^[1-9][0-9]*(?:\.[0-9]+)?[bkmg]?$", re.IGNORECASE)
REQUIRED_ARTIFACT_ENTRIES = (
    "pyproject.toml",
    "uv.lock",
    "wheels",
    "src",
    ".env.detail",
    "config.detail.json",
    "secrets.detail.json",
)
LOCAL_ONLY_NAMES = frozenset(
    {
        ".runtime",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
    }
)
DEFAULT_CPUS = 0.5
DEFAULT_MEMORY = "1g"
DEFAULT_VOLUME_PATH = "/app/volume"
RUNTIME_VOLUME_KEY = "runtime"
ALLOWED_SYSTEM_PROFILES = frozenset({"base", "sqlserver"})


class DistributionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ProcessJob:
    number: str
    name: str

    @property
    def container_name(self) -> str:
        return f"job{self.number}"

    @property
    def config_file(self) -> str:
        return f"processes/{self.name}/config.json"


@dataclass(frozen=True, slots=True)
class ContainerResources:
    cpus: float
    memory: str


PROCESS_JOBS = (
    ProcessJob("01", "pi-web-api"),
    ProcessJob("02", "notpii"),
    ProcessJob("03", "dispatch"),
    ProcessJob("04", "blockgrade"),
    ProcessJob("05", "fabrica"),
    ProcessJob("06", "remanentes"),
    ProcessJob("21", "kpis"),
    ProcessJob("22", "kpis-historian"),
    ProcessJob("41", "kpis-delivery"),
)
PROCESS_JOBS_BY_NAME = {job.name: job for job in PROCESS_JOBS}


def select_jobs(
    processes: tuple[str, ...], *, include_all: bool
) -> tuple[ProcessJob, ...]:
    if include_all and processes:
        raise DistributionError("process names cannot be combined with --all")
    if include_all:
        return PROCESS_JOBS
    if not processes:
        raise DistributionError("select at least one process or use --all")
    if len(processes) != len(set(processes)):
        raise DistributionError("duplicate process selection is not allowed")
    unknown = tuple(name for name in processes if name not in PROCESS_JOBS_BY_NAME)
    if unknown:
        raise DistributionError(f"unknown process selection: {', '.join(unknown)}")
    selected = frozenset(processes)
    return tuple(job for job in PROCESS_JOBS if job.name in selected)


def distribution_root(repository_root: Path, name: str) -> Path:
    if not DISTRIBUTION_NAME_PATTERN.fullmatch(name):
        raise DistributionError(f"invalid distribution name: {name}")
    return repository_root / "distribution" / name


def validate_distribution(
    *,
    repository_root: Path,
    jobs: tuple[ProcessJob, ...],
) -> None:
    docker_root = repository_root / "deployment" / "processes"
    for required in ("Dockerfile", ".dockerignore"):
        path = docker_root / required
        if not path.is_file():
            raise DistributionError(f"process container file not found: {path}")
    local_script = repository_root / "deployment" / "distribution" / "local-process.sh"
    commented_local_script = (
        repository_root
        / "deployment"
        / "distribution"
        / "commented"
        / "local-process.sh"
    )
    for path in (local_script, commented_local_script):
        if not path.is_file():
            raise DistributionError(f"distribution local runner not found: {path}")
    for job in jobs:
        artifact_root = repository_root / "artifacts" / "processes" / job.name
        if not artifact_root.is_dir():
            raise DistributionError(f"process artifact not found: {artifact_root}")
        for relative in REQUIRED_ARTIFACT_ENTRIES:
            path = artifact_root / relative
            if not path.exists():
                raise DistributionError(
                    f"process transport artifact is incomplete ({relative}): {artifact_root}"
                )
        if not (artifact_root / "wheels").is_dir():
            raise DistributionError(
                f"process artifact wheels must be a directory: {artifact_root}"
            )
        if not (artifact_root / "src").is_dir():
            raise DistributionError(
                f"process artifact src must be a directory: {artifact_root}"
            )
        _container_resources(artifact_root / "pyproject.toml")


def distribute(
    *,
    repository_root: Path,
    name: str,
    jobs: tuple[ProcessJob, ...],
) -> Path:
    repository_root = repository_root.resolve()
    target_root = distribution_root(repository_root, name).resolve()
    validate_distribution(repository_root=repository_root, jobs=jobs)
    target_root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{name}.atlanticus-",
        dir=target_root.parent,
    ) as temporary:
        staging_root = Path(temporary)
        staged_processes = staging_root / "processes"
        staged_processes.mkdir()
        docker_root = repository_root / "deployment" / "processes"
        shutil.copy2(docker_root / "Dockerfile", staging_root / "Dockerfile")
        shutil.copy2(docker_root / ".dockerignore", staging_root / ".dockerignore")
        for job in jobs:
            artifact_root = repository_root / "artifacts" / "processes" / job.name
            staged_process = staged_processes / job.name
            shutil.copytree(
                artifact_root,
                staged_process,
                ignore=_artifact_ignore,
            )
            _preserve_consumer_files(target_root, staged_process, job)
            _validate_staged_process(staged_process, job)
        services_path = staging_root / "services.json"
        services_path.write_text(_render_services(jobs), encoding="utf-8")
        _validate_services(services_path, jobs)
        _write_local_deployment(
            repository_root=repository_root,
            staging_root=staging_root,
            distribution_name=name,
            jobs=jobs,
        )
        _validate_local_deployment(staging_root, jobs)
        _replace_directory(staging_root, target_root)
    return target_root


def _artifact_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name in LOCAL_ONLY_NAMES}
    ignored.update(
        name
        for name in names
        if name == ".env" or (name.startswith(".env.") and name != ".env.detail")
    )
    if Path(directory).name in PROCESS_JOBS_BY_NAME:
        ignored.update({"config.json", "secrets.json"})
    ignored.update(name for name in names if name.endswith(".egg-info"))
    return ignored


def _preserve_consumer_files(
    target_root: Path,
    staged_process: Path,
    job: ProcessJob,
) -> None:
    current_process = target_root / "processes" / job.name
    for name in ("config.json", "secrets.json", ".env"):
        current = current_process / name
        if current.is_file():
            shutil.copy2(current, staged_process / name)


def _validate_staged_process(process_root: Path, job: ProcessJob) -> None:
    for relative in REQUIRED_ARTIFACT_ENTRIES:
        if not (process_root / relative).exists():
            raise DistributionError(
                f"staged process is incomplete ({relative}): {process_root}"
            )
    forbidden = tuple(
        path
        for path in process_root.rglob("*")
        if (path.name.startswith(".env.") and path.name != ".env.detail")
        or path.name == ".runtime"
    )
    if forbidden:
        raise DistributionError(f"local-only file reached distribution: {forbidden[0]}")
    if not PROCESS_NAME_PATTERN.fullmatch(job.name):
        raise DistributionError(f"invalid process name in job catalog: {job.name}")


def _service(job: ProcessJob) -> dict[str, object]:
    return {
        "repository": job.name,
        "excecution_file": job.name,
        "container_name": job.container_name,
        "config_file": job.config_file,
        "to_deploy": True,
        "to_stop": False,
        "to_working_hours_dev": False,
        "to_working_hours_uat": True,
    }


def _render_services(jobs: tuple[ProcessJob, ...]) -> str:
    return (
        json.dumps([_service(job) for job in jobs], indent=2, ensure_ascii=False) + "\n"
    )


def _validate_services(path: Path, jobs: tuple[ProcessJob, ...]) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DistributionError(
            f"could not validate generated services file: {path}"
        ) from error
    expected = [_service(job) for job in jobs]
    if payload != expected:
        raise DistributionError(
            f"generated services file does not match selected jobs: {path}"
        )


def _write_local_deployment(
    *,
    repository_root: Path,
    staging_root: Path,
    distribution_name: str,
    jobs: tuple[ProcessJob, ...],
) -> None:
    local_root = staging_root / "local-deployment"
    local_root.mkdir()
    definitions = tuple(
        (
            job,
            _container_resources(
                staging_root / "processes" / job.name / "pyproject.toml"
            ),
        )
        for job in jobs
    )
    (local_root / "compose.yaml").write_text(
        _render_compose(distribution_name, definitions, volume_mode="named"),
        encoding="utf-8",
    )
    (local_root / "compose.bind.yaml").write_text(
        _render_compose(distribution_name, definitions, volume_mode="bind"),
        encoding="utf-8",
    )
    scripts_root = staging_root / "scripts"
    commented_root = scripts_root / "commented"
    commented_root.mkdir(parents=True)
    source = repository_root / "deployment" / "distribution" / "local-process.sh"
    commented_source = (
        repository_root
        / "deployment"
        / "distribution"
        / "commented"
        / "local-process.sh"
    )
    shutil.copy2(source, scripts_root / "local-process.sh")
    shutil.copy2(commented_source, commented_root / "local-process.sh")


def _container_resources(pyproject_path: Path) -> ContainerResources:
    metadata = _read_toml(pyproject_path)
    tool = metadata.get("tool")
    if not isinstance(tool, dict):
        raise DistributionError(f"container metadata is missing: {pyproject_path}")
    atlanticus = tool.get("atlanticus")
    if not isinstance(atlanticus, dict):
        raise DistributionError(f"container metadata is missing: {pyproject_path}")
    container = atlanticus.get("container")
    if not isinstance(container, dict):
        raise DistributionError(f"container metadata is missing: {pyproject_path}")
    command = container.get("command")
    system_profile = container.get("system-profile")
    if not isinstance(command, str) or not command:
        raise DistributionError(f"container command is invalid: {pyproject_path}")
    if system_profile not in ALLOWED_SYSTEM_PROFILES:
        raise DistributionError(
            f"container system profile is invalid: {pyproject_path}"
        )
    resources = container.get("resources", {})
    if not isinstance(resources, dict):
        raise DistributionError(
            f"container resources must be a table: {pyproject_path}"
        )
    cpus = resources.get("cpus", DEFAULT_CPUS)
    memory = resources.get("memory", DEFAULT_MEMORY)
    if isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or cpus <= 0:
        raise DistributionError(
            f"container cpus must be greater than zero: {pyproject_path}"
        )
    if not isinstance(memory, str) or not MEMORY_PATTERN.fullmatch(memory):
        raise DistributionError(f"container memory is invalid: {pyproject_path}")
    return ContainerResources(cpus=float(cpus), memory=memory.lower())


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise DistributionError(f"could not read TOML file: {path}") from error


def _render_compose(
    distribution_name: str,
    definitions: tuple[tuple[ProcessJob, ContainerResources], ...],
    *,
    volume_mode: str,
) -> str:
    if volume_mode not in {"named", "bind"}:
        raise DistributionError(f"unsupported local volume mode: {volume_mode}")
    services = "\n".join(
        _render_local_service(
            distribution_name, job, resources, volume_mode=volume_mode
        )
        for job, resources in definitions
    )
    suffix = f"\nvolumes:\n  {RUNTIME_VOLUME_KEY}:\n" if volume_mode == "named" else ""
    return (
        f"name: atlanticus-{distribution_name}-local\nservices:\n{services}\n{suffix}"
    )


def _render_local_service(
    distribution_name: str,
    job: ProcessJob,
    resources: ContainerResources,
    *,
    volume_mode: str,
) -> str:
    volume_source = RUNTIME_VOLUME_KEY if volume_mode == "named" else "./runtime"
    return "\n".join(
        (
            f"  {job.name}:",
            f"    image: atlanticus-{distribution_name}-{job.name}:local",
            "    build:",
            "      context: ..",
            "      dockerfile: Dockerfile",
            "      args:",
            f"        FILENAME: {job.name}",
            '    command: ["--run-once"]',
            "    env_file:",
            f"      - ../processes/{job.name}/.env",
            "    environment:",
            f"      VOLUMEN_PATH: {DEFAULT_VOLUME_PATH}",
            "    volumes:",
            f"      - {volume_source}:{DEFAULT_VOLUME_PATH}",
            f"    cpus: {resources.cpus:g}",
            f"    mem_limit: {resources.memory}",
        )
    )


def _validate_local_deployment(
    staging_root: Path, jobs: tuple[ProcessJob, ...]
) -> None:
    local_root = staging_root / "local-deployment"
    for compose_name in ("compose.yaml", "compose.bind.yaml"):
        compose = (local_root / compose_name).read_text(encoding="utf-8")
        for job in jobs:
            if f"  {job.name}:" not in compose:
                raise DistributionError(
                    f"generated local Compose is missing service: {job.name}"
                )
            if f"FILENAME: {job.name}" not in compose:
                raise DistributionError(
                    f"generated local Compose has invalid selector: {job.name}"
                )
            if f"../processes/{job.name}/.env" not in compose:
                raise DistributionError(
                    f"generated local Compose has invalid env path: {job.name}"
                )
        if 'command: ["--run-once"]' not in compose:
            raise DistributionError(
                f"generated local Compose is not configured for run-once: {compose_name}"
            )
    if not (staging_root / "scripts" / "local-process.sh").is_file():
        raise DistributionError("generated distribution local runner is missing")


def _replace_directory(source: Path, target: Path) -> None:
    replacement = target.with_name(f".{target.name}.atlanticus-{uuid.uuid4().hex}.new")
    backup = target.with_name(f".{target.name}.atlanticus-{uuid.uuid4().hex}.backup")
    shutil.copytree(source, replacement)
    had_target = target.exists()
    try:
        if had_target:
            os.replace(target, backup)
        os.replace(replacement, target)
    except BaseException:
        if replacement.exists():
            shutil.rmtree(replacement)
        if had_target and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _repository_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a self-contained Atlanticus consumer distribution."
    )
    parser.add_argument("distribution")
    parser.add_argument("processes", nargs="*")
    parser.add_argument(
        "--repository-root", type=Path, default=_repository_root_from_script()
    )
    parser.add_argument("--all", action="store_true", dest="include_all")
    return parser.parse_args()


def main() -> None:
    arguments = _parse_arguments()
    jobs = select_jobs(tuple(arguments.processes), include_all=arguments.include_all)
    repository_root = arguments.repository_root.resolve()
    target_root = distribute(
        repository_root=repository_root,
        name=arguments.distribution,
        jobs=jobs,
    )
    rendered_jobs = ", ".join(f"{job.container_name}={job.name}" for job in jobs)
    print(f"Distributed processes: {rendered_jobs}")
    print(f"Distribution package: {target_root}")
    missing_configs = tuple(
        job.config_file for job in jobs if not (target_root / job.config_file).is_file()
    )
    if missing_configs:
        print("Consumer configuration required:")
        for path in missing_configs:
            print(f"  - {path}")
    missing_secrets = tuple(
        f"processes/{job.name}/secrets.json"
        for job in jobs
        if not (target_root / "processes" / job.name / "secrets.json").is_file()
    )
    if missing_secrets:
        print("Consumer secrets manifest required:")
        for path in missing_secrets:
            print(f"  - {path}")
    missing_envs = tuple(
        f"processes/{job.name}/.env"
        for job in jobs
        if not (target_root / "processes" / job.name / ".env").is_file()
    )
    if missing_envs:
        print("Local E2E environment required:")
        for path in missing_envs:
            print(f"  - {path}")
    print(f"Local E2E: {target_root / 'scripts' / 'local-process.sh'} up")


if __name__ == "__main__":
    try:
        main()
    except DistributionError as error:
        raise SystemExit(str(error)) from error
