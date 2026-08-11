# El layout describe la composición lógica, no nombres de archivos ni un formato físico.
"""Layouts neutrales para una unidad lógica de publicación."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from atlanticus.datasets.errors import DatasetDefinitionError
from atlanticus.datasets.validation import validate_dimension_name


@dataclass(frozen=True, slots=True)
class SingleArtifactLayout:
    """La publicación confirmada se representa mediante un único artefacto."""


@dataclass(frozen=True, slots=True)
class FileSetLayout:
    """La publicación confirmada contiene partes identificadas por una dimensión."""

    # La dimensión es semántica; el adaptador puede conservar nombres físicos opacos.
    part_dimension: str

    def __post_init__(self) -> None:
        validate_dimension_name(
            self.part_dimension,
            field='part_dimension',
            error_type=DatasetDefinitionError,
        )


DatasetLayout: TypeAlias = SingleArtifactLayout | FileSetLayout
