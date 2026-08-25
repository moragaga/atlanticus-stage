# Este módulo implementa sólo el algoritmo de resolución.
# No conoce filesystem, AtomicJsonStore, Cosmos ni Job Runtime.
from __future__ import annotations

from dataclasses import dataclass

from ada.processes.alarms_runtime.revision_resolution import (
    RuntimeRevisionBundle,
    RuntimeRevisionCache,
    RuntimeRevisionCacheError,
    RuntimeRevisionContractError,
    RuntimeRevisionDecoder,
    RuntimeRevisionOrigin,
    RuntimeRevisionResolution,
    RuntimeRevisionSource,
    RuntimeRevisionSourceError,
)


class RuntimeRevisionResolverError(RuntimeError):
    pass


# El resolver compone las tres estrategias cerradas en R3.4A.
@dataclass(frozen=True, slots=True)
class RuntimeRevisionResolver:
    source: RuntimeRevisionSource
    decoder: RuntimeRevisionDecoder
    cache: RuntimeRevisionCache

    def __post_init__(self) -> None:
        if not isinstance(self.source, RuntimeRevisionSource):
            raise TypeError('source must implement RuntimeRevisionSource')
        if not isinstance(self.decoder, RuntimeRevisionDecoder):
            raise TypeError('decoder must implement RuntimeRevisionDecoder')
        if not isinstance(self.cache, RuntimeRevisionCache):
            raise TypeError('cache must implement RuntimeRevisionCache')

    def resolve(self) -> RuntimeRevisionResolution:
        # La copia efectiva se valida antes de consultar Source.
        # Si está corrupta, no se intenta reconstruirla de manera heurística.
        cached = self._load_cached_resolution()
        try:
            manifest = self.source.read_manifest()
        except RuntimeRevisionSourceError as error:
            # Una caída de Source puede degradar a last-known-good sólo si el cache es válido.
            return self._fallback_or_raise(
                cached,
                error,
                'published runtime manifest is unavailable and no effective cache exists',
            )
        # Misma pareja AC/TR implica que los documentos cacheados siguen siendo autoridad efectiva.
        if cached is not None and manifest.revision_key == cached.revision_key:
            return RuntimeRevisionResolution(
                bundle=cached.bundle,
                revision=cached.revision,
                origin=RuntimeRevisionOrigin.CACHE_CURRENT,
            )
        try:
            # La configuración y el registro se solicitan por los IDs exactos del manifest.
            bundle = RuntimeRevisionBundle(
                manifest=manifest,
                alarm_configuration=self.source.read_alarm_configuration(
                    revision=manifest.alarm_configuration_revision
                ),
                tool_registry=self.source.read_tool_registry(
                    revision=manifest.tool_registry_revision
                ),
            )
            # El decoder sigue siendo dueño de la validación semántica y cross-validation.
            revision = self.decoder.decode(bundle=bundle)
            return RuntimeRevisionResolution(
                bundle=bundle,
                revision=revision,
                origin=RuntimeRevisionOrigin.SOURCE_CANDIDATE,
            )
        except (RuntimeRevisionSourceError, RuntimeRevisionContractError, ValueError) as error:
            # Un target incompleto o inválido nunca reemplaza el último epoch válido.
            return self._fallback_or_raise(
                cached,
                error,
                'published runtime revision is invalid and no effective cache exists',
            )

    def _load_cached_resolution(self) -> RuntimeRevisionResolution | None:
        bundle = self.cache.load_effective()
        if bundle is None:
            return None
        try:
            # El cache físico también debe atravesar el mismo decoder que Source.
            revision = self.decoder.decode(bundle=bundle)
            return RuntimeRevisionResolution(
                bundle=bundle,
                revision=revision,
                origin=RuntimeRevisionOrigin.CACHE_CURRENT,
            )
        except (RuntimeRevisionContractError, ValueError) as error:
            raise RuntimeRevisionCacheError(
                'effective runtime revision cache is invalid'
            ) from error

    @staticmethod
    def _fallback_or_raise(
        cached: RuntimeRevisionResolution | None,
        error: Exception,
        message: str,
    ) -> RuntimeRevisionResolution:
        if cached is None:
            raise RuntimeRevisionResolverError(message) from error
        return RuntimeRevisionResolution(
            bundle=cached.bundle,
            revision=cached.revision,
            origin=RuntimeRevisionOrigin.CACHE_FALLBACK,
        )
