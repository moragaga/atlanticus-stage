from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# API pública mínima del proceso: catálogo, composición y ejecución.
__version__ = '0.1.1'

__all__ = [
    '__version__',
    'build_catalog',
    'build_composition',
    'run',
]


def build_catalog() -> Any:
    from ada.processes.notpii.catalog import build_catalog as _build_catalog

    return _build_catalog()


def build_composition(*, configuration: Any, catalog: Any = None) -> Any:
    from ada.processes.notpii.composition import build_composition as _build_composition

    return _build_composition(configuration=configuration, catalog=catalog)


def run(
    *,
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    process_root: str | Path | None = None,
) -> Any:
    from ada.processes.notpii.bootstrap import run as _run

    return _run(argv=argv, environ=environ, process_root=process_root)
