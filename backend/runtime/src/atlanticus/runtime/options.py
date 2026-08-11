"""Argumentos cerrados permitidos por el ejecutable de un job."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass

from atlanticus.runtime.definition import JobDefinition


@dataclass(frozen=True, slots=True)
class RuntimeOptions:
    environment: str | None
    debug: bool
    run_once: bool

    def __post_init__(self) -> None:
        if self.environment is not None and not isinstance(self.environment, str):
            raise TypeError('environment must be a string')
        if not isinstance(self.debug, bool):
            raise TypeError('debug must be a bool')
        if not isinstance(self.run_once, bool):
            raise TypeError('run_once must be a bool')


def parse_runtime_options(
    *,
    definition: JobDefinition,
    argv: Sequence[str] | None = None,
) -> RuntimeOptions:
    """Rechaza argumentos desconocidos y conserva tiempos en la definición."""

    if not isinstance(definition, JobDefinition):
        raise TypeError('definition must be a JobDefinition')
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--environment', default=None)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--run-once', action='store_true')
    arguments = parser.parse_args(argv)
    debug = bool(arguments.debug)
    return RuntimeOptions(
        environment=arguments.environment,
        debug=debug,
        run_once=bool(definition.run_once or arguments.run_once or debug),
    )
