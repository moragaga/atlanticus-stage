from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

__version__ = '0.1.1'

__all__ = [
    '__version__',
    'build_catalog',
    'build_composition',
    'run',
]


def build_catalog() -> tuple[Any, ...]:
    from ada.processes.fabrica.catalog import build_catalog as _build_catalog

    return _build_catalog()


def build_composition(*, configuration: Any, catalog: tuple[Any, ...] | None = None) -> Any:
    from ada.processes.fabrica.composition import build_composition as _build_composition

    return _build_composition(configuration=configuration, catalog=catalog)


def run(
    *,
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    process_root: str | Path | None = None,
) -> Any:
    from ada.processes.fabrica.bootstrap import run as _run

    return _run(argv=argv, environ=environ, process_root=process_root)
