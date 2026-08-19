from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime

from ada.kpis.core import KpiEvaluation, KpiWatermark
from ada.kpis.persistence.errors import (
    KpiEvaluationConflictError,
    KpiPersistenceCorruptionError,
)
from ada.kpis.persistence.models import KpiEvaluationWriteStatus
from ada.kpis.persistence.paths import KpiPersistencePaths
from atlanticus.json import JsonConflictError, JsonDocumentStore, JsonWriteOnceStatus

_FILENAME_PATTERN = re.compile(r'^(\d{8}T\d{6}Z)\.json$')


class KpiEvaluationRepository:
    def __init__(self, *, store: JsonDocumentStore, paths: KpiPersistencePaths) -> None:
        if not isinstance(store, JsonDocumentStore):
            raise TypeError('store must be JsonDocumentStore')
        if not isinstance(paths, KpiPersistencePaths):
            raise TypeError('paths must be KpiPersistencePaths')
        self._store = store
        self._paths = paths

    def write_once(self, evaluation: KpiEvaluation) -> KpiEvaluationWriteStatus:
        if not isinstance(evaluation, KpiEvaluation):
            raise TypeError('evaluation must be KpiEvaluation')
        path = self._paths.evaluation_path(evaluation.watermark)
        try:
            status = self._store.write_once(path, evaluation.as_document())
        except JsonConflictError as error:
            raise KpiEvaluationConflictError(
                f'{evaluation.watermark.text}: evaluation already exists with different content'
            ) from error
        if status is JsonWriteOnceStatus.CREATED:
            return KpiEvaluationWriteStatus.CREATED
        return KpiEvaluationWriteStatus.UNCHANGED

    def read(self, watermark: KpiWatermark) -> KpiEvaluation | None:
        if not isinstance(watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        document = self._store.read(self._paths.evaluation_path(watermark))
        if document is None:
            return None
        evaluation = _evaluation_from_document(document)
        if evaluation.watermark != watermark:
            raise KpiPersistenceCorruptionError(
                f'{watermark.text}: evaluation document watermark does not match its path'
            )
        return evaluation

    def iter_between(
        self,
        *,
        after: KpiWatermark | None = None,
        through: KpiWatermark | None = None,
    ) -> Iterator[KpiEvaluation]:
        _optional_watermark(after, 'after')
        _optional_watermark(through, 'through')
        if after is not None and through is not None and after > through:
            raise ValueError('after must not be greater than through')
        root = self._paths.evaluations_root
        if not root.exists():
            return
        candidates: list[tuple[KpiWatermark, object]] = []
        for path in root.glob('year=*/month=*/day=*/*.json'):
            watermark = _watermark_from_path_name(path.name)
            if after is not None and watermark <= after:
                continue
            if through is not None and watermark > through:
                continue
            candidates.append((watermark, path))
        for watermark, path in sorted(candidates, key=lambda item: item[0]):
            document = self._store.read(path)
            if document is None:
                continue
            evaluation = _evaluation_from_document(document)
            if evaluation.watermark != watermark:
                raise KpiPersistenceCorruptionError(
                    f'{watermark.text}: evaluation document watermark does not match its path'
                )
            yield evaluation


class KpiLatestRepository:
    def __init__(self, *, store: JsonDocumentStore, paths: KpiPersistencePaths) -> None:
        if not isinstance(store, JsonDocumentStore):
            raise TypeError('store must be JsonDocumentStore')
        if not isinstance(paths, KpiPersistencePaths):
            raise TypeError('paths must be KpiPersistencePaths')
        self._store = store
        self._paths = paths

    def replace(self, evaluation: KpiEvaluation) -> None:
        if not isinstance(evaluation, KpiEvaluation):
            raise TypeError('evaluation must be KpiEvaluation')
        self._store.replace(self._paths.latest_path, evaluation.as_document())

    def read(self) -> KpiEvaluation | None:
        document = self._store.read(self._paths.latest_path)
        return None if document is None else _evaluation_from_document(document)


def _evaluation_from_document(document: object) -> KpiEvaluation:
    try:
        return KpiEvaluation.from_document(document)
    except (TypeError, ValueError) as error:
        raise KpiPersistenceCorruptionError('persisted KPI evaluation is invalid') from error


def _watermark_from_path_name(name: str) -> KpiWatermark:
    match = _FILENAME_PATTERN.fullmatch(name)
    if match is None:
        raise KpiPersistenceCorruptionError(f'invalid KPI evaluation filename: {name}')
    try:
        timestamp = datetime.strptime(match.group(1), '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)
    except ValueError as error:
        raise KpiPersistenceCorruptionError(f'invalid KPI evaluation filename: {name}') from error
    return KpiWatermark(timestamp)


def _optional_watermark(value: object, field_name: str) -> None:
    if value is not None and not isinstance(value, KpiWatermark):
        raise TypeError(f'{field_name} must be KpiWatermark or None')
