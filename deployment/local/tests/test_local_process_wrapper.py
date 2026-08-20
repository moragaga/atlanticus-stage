from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _prepare_wrapper(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT / "scripts/local-process.sh", scripts / "local-process.sh"
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv = fake_bin / "uv"
    uv.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$UV_ARGS_FILE"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)

    args_file = tmp_path / "uv-args.txt"
    environ = dict(os.environ)
    environ["PATH"] = f"{fake_bin}{os.pathsep}{environ.get('PATH', '')}"
    environ["UV_ARGS_FILE"] = str(args_file)
    return scripts / "local-process.sh", args_file, environ


def _run_prepare(
    tmp_path: Path,
    *arguments: str,
) -> tuple[subprocess.CompletedProcess[str], tuple[str, ...]]:
    wrapper, args_file, environ = _prepare_wrapper(tmp_path)
    result = subprocess.run(
        ("bash", str(wrapper), "prepare", *arguments),
        cwd=wrapper.parents[1],
        env=environ,
        check=False,
        capture_output=True,
        text=True,
    )
    uv_arguments = (
        tuple(args_file.read_text(encoding="utf-8").splitlines())
        if args_file.is_file()
        else ()
    )
    return result, uv_arguments


def test_prepare_without_arguments_keeps_all_processes_behavior(tmp_path: Path) -> None:
    result, uv_arguments = _run_prepare(tmp_path)

    assert result.returncode == 0
    assert "--all" not in uv_arguments
    assert "process_bundle.py" in " ".join(uv_arguments)


def test_prepare_all_normalizes_to_bundler_discovery(tmp_path: Path) -> None:
    result, uv_arguments = _run_prepare(tmp_path, "--all")

    assert result.returncode == 0
    assert "--all" not in uv_arguments
    assert "process_bundle.py" in " ".join(uv_arguments)


def test_prepare_forwards_selected_processes_to_bundler(tmp_path: Path) -> None:
    result, uv_arguments = _run_prepare(
        tmp_path,
        "kpis",
        "kpis-historian",
        "kpis-delivery",
    )

    assert result.returncode == 0
    assert "kpis" in uv_arguments
    assert "kpis-historian" in uv_arguments
    assert "kpis-delivery" in uv_arguments


def test_prepare_rejects_all_mixed_with_processes(tmp_path: Path) -> None:
    result, uv_arguments = _run_prepare(tmp_path, "--all", "kpis")

    assert result.returncode == 1
    assert uv_arguments == ()
    assert "prepare [--all|PROCESS [PROCESS ...]]" in result.stderr
