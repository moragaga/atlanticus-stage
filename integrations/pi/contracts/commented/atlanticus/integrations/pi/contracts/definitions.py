from dataclasses import dataclass

from .enums import PiExtractionMode, PiMaterialization, PiValueKind


# Conserva nombres y aliases exactamente como fueron declarados; no normaliza silenciosamente.
def _require_identifier(value: str, *, field_name: str) -> None:
    if not value:
        raise ValueError(f'{field_name} must not be empty.')
    if value != value.strip():
        raise ValueError(f'{field_name} must not contain surrounding whitespace.')


# Fuente NOTPII: marcador explícito sin parámetros ajenos a ese mecanismo de adquisición.
@dataclass(frozen=True, slots=True)
class NotPiiSource:
    pass


# Fuente PI Web API: una única interpolación compartida por todo el catálogo que la usa.
@dataclass(frozen=True, slots=True)
class PiWebApiSource:
    interpolation_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.interpolation_seconds is None:
            return
        if (
            isinstance(self.interpolation_seconds, bool)
            or not isinstance(self.interpolation_seconds, int)
            or self.interpolation_seconds <= 0
        ):
            raise ValueError('interpolation_seconds must be a positive integer.')


# Unión cerrada de fuentes admitidas por el contrato PI actual.
PiSource = NotPiiSource | PiWebApiSource


# Define el tag real de PI y qué materializaciones debe producir; no conoce conexiones ni WebID.
@dataclass(frozen=True, slots=True)
class PiTagDefinition:
    tag_name: str
    alias: str
    value_kind: PiValueKind
    extraction_mode: PiExtractionMode
    materializations: tuple[PiMaterialization, ...]
    is_active: bool = True

    def __post_init__(self) -> None:
        _require_identifier(self.tag_name, field_name='tag_name')
        _require_identifier(self.alias, field_name='alias')
        if not isinstance(self.value_kind, PiValueKind):
            raise TypeError('value_kind must be a PiValueKind.')
        if not isinstance(self.extraction_mode, PiExtractionMode):
            raise TypeError('extraction_mode must be a PiExtractionMode.')
        if not isinstance(self.materializations, tuple):
            raise TypeError('materializations must be a tuple.')
        if not self.materializations:
            raise ValueError('materializations must not be empty.')
        if any(not isinstance(item, PiMaterialization) for item in self.materializations):
            raise TypeError('materializations must contain only PiMaterialization values.')
        if len(set(self.materializations)) != len(self.materializations):
            raise ValueError('materializations must not contain duplicates.')
        if not isinstance(self.is_active, bool):
            raise TypeError('is_active must be a bool.')
        if (
            self.extraction_mode is PiExtractionMode.RECORDED
            and PiMaterialization.LATEST in self.materializations
        ):
            raise ValueError('Recorded PI tags cannot declare latest materialization.')


# Compone una única fuente con todas las definiciones que se ejecutan bajo ese contrato.
@dataclass(frozen=True, slots=True)
class PiCatalog:
    source: PiSource
    definitions: tuple[PiTagDefinition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source, (NotPiiSource, PiWebApiSource)):
            raise TypeError('source must be a NotPiiSource or PiWebApiSource.')
        if not isinstance(self.definitions, tuple):
            raise TypeError('definitions must be a tuple.')
        if not self.definitions:
            raise ValueError('definitions must not be empty.')
        if any(not isinstance(item, PiTagDefinition) for item in self.definitions):
            raise TypeError('definitions must contain only PiTagDefinition values.')

        # El alias termina como nombre de columna; duplicarlo produciría un pivot ambiguo.
        aliases = [definition.alias for definition in self.definitions]
        if len(set(aliases)) != len(aliases):
            raise ValueError('definitions must use unique aliases.')

        # PI Web API necesita una sola grilla común cuando existen tags interpolados activos.
        if isinstance(self.source, PiWebApiSource):
            has_interpolated = any(
                definition.is_active and definition.extraction_mode is PiExtractionMode.INTERPOLATED
                for definition in self.definitions
            )
            if has_interpolated and self.source.interpolation_seconds is None:
                raise ValueError(
                    'PiWebApiSource requires interpolation_seconds when the catalog '
                    'contains active interpolated tags.'
                )
