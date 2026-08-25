from __future__ import annotations

from dataclasses import dataclass

from ada.processes.alarms_runtime.revision_resolution import (
    ResolvedRuntimeRevision,
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
        cached = self._load_cached_resolution()
        try:
            manifest = self.source.read_manifest()
        except RuntimeRevisionSourceError as error:
            return self._fallback_or_raise(
                cached,
                error,
                'published runtime manifest is unavailable and no effective cache exists',
            )
        if cached is not None and manifest.revision_key == cached.revision_key:
            return RuntimeRevisionResolution(
                origin=RuntimeRevisionOrigin.CACHE_CURRENT,
                effective=cached,
                target=cached,
            )
        try:
            bundle = RuntimeRevisionBundle(
                manifest=manifest,
                alarm_configuration=self.source.read_alarm_configuration(
                    revision=manifest.alarm_configuration_revision
                ),
                tool_registry=self.source.read_tool_registry(
                    revision=manifest.tool_registry_revision
                ),
            )
            target = ResolvedRuntimeRevision(
                bundle=bundle,
                revision=self.decoder.decode(bundle=bundle),
            )
            return RuntimeRevisionResolution(
                origin=RuntimeRevisionOrigin.SOURCE_CANDIDATE,
                effective=cached,
                target=target,
            )
        except (RuntimeRevisionSourceError, RuntimeRevisionContractError, ValueError) as error:
            return self._fallback_or_raise(
                cached,
                error,
                'published runtime revision is invalid and no effective cache exists',
            )

    def _load_cached_resolution(self) -> ResolvedRuntimeRevision | None:
        bundle = self.cache.load_effective()
        if bundle is None:
            return None
        try:
            return ResolvedRuntimeRevision(
                bundle=bundle,
                revision=self.decoder.decode(bundle=bundle),
            )
        except (RuntimeRevisionContractError, ValueError) as error:
            raise RuntimeRevisionCacheError(
                'effective runtime revision cache is invalid'
            ) from error

    @staticmethod
    def _fallback_or_raise(
        cached: ResolvedRuntimeRevision | None,
        error: Exception,
        message: str,
    ) -> RuntimeRevisionResolution:
        if cached is None:
            raise RuntimeRevisionResolverError(message) from error
        return RuntimeRevisionResolution(
            origin=RuntimeRevisionOrigin.CACHE_FALLBACK,
            effective=cached,
            target=cached,
        )
