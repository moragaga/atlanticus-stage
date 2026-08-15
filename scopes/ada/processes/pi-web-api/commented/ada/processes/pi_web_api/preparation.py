# Espejo pedagógico: la resolución inicial de WebIDs usa la misma política de timeout y conserva por chunk los WebIDs ya resueltos.
from __future__ import annotations

from typing import Protocol

from ada.processes.pi_web_api.errors import (
    PiWebApiCatalogError,
    PiWebApiTimeoutExhaustedError,
)
from ada.processes.pi_web_api.models import (
    PiExecutionPlan,
    PiPreparationResult,
    ResolvedPiTag,
)
from ada.processes.pi_web_api.timeout_retry import execute_with_timeout_retries
from ada.processes.pi_web_api.web_ids import WebIdRegistry
from atlanticus.integrations.pi.contracts import (
    PiCatalog,
    PiExtractionMode,
    PiTagDefinition,
    PiWebApiSource,
)
from atlanticus.integrations.pi.web_api import PiPointWebIdResult, PiWebApiLimits
from atlanticus.runtime import JobRuntimeContext


class _PointResource(Protocol):
    def resolve_web_ids(self, tag_names: tuple[str, ...]) -> tuple[PiPointWebIdResult, ...]: ...


class _ClientSettings(Protocol):
    limits: PiWebApiLimits


class _PiWebApiClient(Protocol):
    settings: _ClientSettings
    points: _PointResource


class PiExecutionPlanPreparer:
    def __init__(self, *, client: _PiWebApiClient, registry: WebIdRegistry) -> None:
        if not hasattr(client, 'points') or not hasattr(client, 'settings'):
            raise TypeError('client must expose points and settings')
        if not isinstance(registry, WebIdRegistry):
            raise TypeError('registry must be a WebIdRegistry')
        self._client = client
        self._registry = registry

    def prepare(
        self,
        catalog: PiCatalog,
        *,
        context: JobRuntimeContext | None = None,
    ) -> PiPreparationResult:
        definitions = _active_definitions(catalog)
        tag_names = tuple(item.tag_name for item in definitions)
        cached = self._registry.lookup(tag_names)
        missing = tuple(tag_name for tag_name in tag_names if tag_name not in cached)
        newly_resolved: dict[str, str] = {}
        unresolved: list[str] = []
        point_request_count = 0
        limit = self._client.settings.limits.points_max_paths

        for offset in range(0, len(missing), limit):
            chunk = missing[offset : offset + limit]
            if not chunk:
                continue
            if context is None:
                point_request_count += 1
                results = self._client.points.resolve_web_ids(chunk)
            else:
                try:
                    results, retry_count = execute_with_timeout_retries(
                        lambda current_chunk=chunk: self._client.points.resolve_web_ids(
                            current_chunk
                        ),
                        context=context,
                        operation_name='points.resolve_web_ids',
                        attributes={'tag_count': len(chunk)},
                    )
                except PiWebApiTimeoutExhaustedError as error:
                    point_request_count += error.retry_count + 1
                    raise PiWebApiTimeoutExhaustedError(
                        phase=error.phase,
                        retry_count=error.retry_count,
                        point_request_count=point_request_count,
                    ) from None
                point_request_count += retry_count + 1
            result_by_name = {item.tag_name: item for item in results}
            resolved_chunk: dict[str, str] = {}
            for tag_name in chunk:
                result = result_by_name.get(tag_name)
                if result is None or result.web_id is None:
                    unresolved.append(tag_name)
                    continue
                resolved_chunk[tag_name] = result.web_id
            if resolved_chunk:
                self._registry.merge(resolved_chunk)
                newly_resolved.update(resolved_chunk)

        entries = self._registry.current()
        unresolved_set = {item.casefold() for item in unresolved}
        interpolated: list[ResolvedPiTag] = []
        recorded: list[ResolvedPiTag] = []
        for definition in definitions:
            web_id = entries.get(definition.tag_name)
            if web_id is None:
                if definition.tag_name.casefold() not in unresolved_set:
                    unresolved.append(definition.tag_name)
                    unresolved_set.add(definition.tag_name.casefold())
                continue
            resolved = ResolvedPiTag(definition=definition, web_id=web_id)
            if definition.extraction_mode is PiExtractionMode.INTERPOLATED:
                interpolated.append(resolved)
            else:
                recorded.append(resolved)

        plan = PiExecutionPlan(
            interpolated=tuple(interpolated),
            recorded=tuple(recorded),
            unresolved_tag_names=tuple(unresolved),
        )
        return PiPreparationResult(
            plan=plan,
            cache_hit_count=len(cached),
            resolved_count=len(newly_resolved),
            unresolved_count=len(unresolved),
            point_request_count=point_request_count,
        )


def _active_definitions(catalog: PiCatalog) -> tuple[PiTagDefinition, ...]:
    if not isinstance(catalog, PiCatalog):
        raise PiWebApiCatalogError('catalog must be a PiCatalog')
    if not isinstance(catalog.source, PiWebApiSource):
        raise PiWebApiCatalogError('catalog source must be PiWebApiSource')
    definitions = tuple(item for item in catalog.definitions if item.is_active)
    if not definitions:
        raise PiWebApiCatalogError('PI Web API catalog must contain active definitions')
    normalized_names = [item.tag_name.casefold() for item in definitions]
    if len(set(normalized_names)) != len(normalized_names):
        raise PiWebApiCatalogError('PI Web API catalog must use unique active tag names')
    return definitions
