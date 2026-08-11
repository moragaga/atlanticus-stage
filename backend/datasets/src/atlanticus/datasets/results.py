"""Resultados neutrales de publicaciones individuales y por lote."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from atlanticus.datasets.errors import DatasetValidationError
from atlanticus.datasets.models import DatasetTarget


class PublicationStatus(StrEnum):
    """Efecto técnico de una solicitud sobre la publicación confirmada."""

    COMMITTED = 'committed'
    UNCHANGED = 'unchanged'
    SKIPPED = 'skipped'


class PublicationQuality(StrEnum):
    """Completitud utilizable del contenido, independiente de su atomicidad."""

    SUCCESS = 'success'
    WARNING = 'warning'


class PublicationSkipReason(StrEnum):
    """Motivos controlados que impiden iniciar una escritura."""

    EMPTY_CONTENT = 'empty_content'


class DatasetBatchStatus(StrEnum):
    """Resumen operacional de un lote de destinos independientes."""

    SUCCESS = 'success'
    WARNING = 'warning'
    FAILED = 'failed'


@dataclass(frozen=True, slots=True)
class DatasetPublicationResult:
    """Resultado compacto de una unidad atómica, sin detalles propios del formato."""

    target: DatasetTarget
    status: PublicationStatus
    quality: PublicationQuality
    finished_at_utc: datetime
    duration_ms: float
    item_count: int
    artifact_count: int
    size_bytes: int | None = None
    content_signature: str | None = None
    warning_count: int = 0
    skip_reason: PublicationSkipReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, DatasetTarget):
            raise DatasetValidationError('publication result must reference a DatasetTarget')
        _validate_enum(self.status, PublicationStatus, field='status')
        _validate_enum(self.quality, PublicationQuality, field='quality')
        _validate_utc(self.finished_at_utc, field='finished_at_utc')
        object.__setattr__(self, 'finished_at_utc', self.finished_at_utc.astimezone(UTC))
        _validate_non_negative_number(self.duration_ms, field='duration_ms')
        _validate_non_negative_integer(self.item_count, field='item_count')
        _validate_non_negative_integer(self.artifact_count, field='artifact_count')
        _validate_non_negative_integer(self.warning_count, field='warning_count')
        if self.size_bytes is not None:
            _validate_non_negative_integer(self.size_bytes, field='size_bytes')
        if self.content_signature is not None and (
            not isinstance(self.content_signature, str) or not self.content_signature.strip()
        ):
            raise DatasetValidationError('content_signature must be a non-empty string or None')
        if self.quality is PublicationQuality.SUCCESS and self.warning_count != 0:
            raise DatasetValidationError('success quality must not contain warnings')
        if self.status is PublicationStatus.SKIPPED:
            self._validate_skipped()
        else:
            self._validate_confirmed()

    @classmethod
    def skipped_empty(
        cls,
        *,
        target: DatasetTarget,
        finished_at_utc: datetime,
        duration_ms: float = 0.0,
    ) -> Self:
        """Representa un vacío detectado antes de cualquier intento de escritura."""

        return cls(
            target=target,
            status=PublicationStatus.SKIPPED,
            quality=PublicationQuality.WARNING,
            finished_at_utc=finished_at_utc,
            duration_ms=duration_ms,
            item_count=0,
            artifact_count=0,
            warning_count=1,
            skip_reason=PublicationSkipReason.EMPTY_CONTENT,
        )

    def _validate_skipped(self) -> None:
        _validate_enum(self.skip_reason, PublicationSkipReason, field='skip_reason')
        if self.skip_reason is not PublicationSkipReason.EMPTY_CONTENT:
            raise DatasetValidationError('unsupported publication skip reason')
        if self.quality is not PublicationQuality.WARNING or self.warning_count < 1:
            raise DatasetValidationError('empty content must be skipped with warning quality')
        if self.item_count != 0 or self.artifact_count != 0:
            raise DatasetValidationError(
                'skipped empty content must not contain items or artifacts'
            )
        if self.size_bytes is not None or self.content_signature is not None:
            raise DatasetValidationError('skipped empty content must not contain write metadata')

    def _validate_confirmed(self) -> None:
        if self.skip_reason is not None:
            raise DatasetValidationError('confirmed publications must not contain a skip reason')
        if self.item_count < 1:
            raise DatasetValidationError('committed or unchanged publications must contain items')
        if self.artifact_count < 1:
            raise DatasetValidationError(
                'committed or unchanged publications must contain artifacts'
            )
        if self.size_bytes is not None and self.size_bytes < 1:
            raise DatasetValidationError(
                'committed or unchanged publication size must be positive when provided'
            )


@dataclass(frozen=True, slots=True)
class DatasetPublicationFailure:
    """Fallo compacto de un target; el detalle extenso permanece en pipeline control."""

    target: DatasetTarget
    error_type: str
    message: str
    duration_ms: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, DatasetTarget):
            raise DatasetValidationError('publication failure must reference a DatasetTarget')
        for field, value in (('error_type', self.error_type), ('message', self.message)):
            if not isinstance(value, str) or not value.strip():
                raise DatasetValidationError(f'{field} must be a non-empty string')
        if self.duration_ms is not None:
            _validate_non_negative_number(self.duration_ms, field='duration_ms')

    @classmethod
    def from_exception(
        cls,
        *,
        target: DatasetTarget,
        error: Exception,
        duration_ms: float | None = None,
    ) -> Self:
        """Reduce una excepción a información segura para el resumen del lote."""

        return cls(
            target=target,
            error_type=error.__class__.__name__,
            message='dataset publication failed',
            duration_ms=duration_ms,
        )


@dataclass(frozen=True, slots=True)
class DatasetBatchResult:
    """Agrega destinos independientes sin simular una transacción global."""

    publications: tuple[DatasetPublicationResult, ...] = ()
    failures: tuple[DatasetPublicationFailure, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.publications, str | bytes):
            raise DatasetValidationError('publications must be an iterable of results')
        if isinstance(self.failures, str | bytes):
            raise DatasetValidationError('failures must be an iterable of failures')
        try:
            publications = tuple(self.publications)
            failures = tuple(self.failures)
        except TypeError as error:
            raise DatasetValidationError('batch values must be iterable') from error
        object.__setattr__(self, 'publications', publications)
        object.__setattr__(self, 'failures', failures)
        if not all(isinstance(item, DatasetPublicationResult) for item in publications):
            raise DatasetValidationError(
                'publications must contain only DatasetPublicationResult values'
            )
        if not all(isinstance(item, DatasetPublicationFailure) for item in failures):
            raise DatasetValidationError(
                'failures must contain only DatasetPublicationFailure values'
            )
        targets = [item.target for item in (*publications, *failures)]
        if len(set(targets)) != len(targets):
            raise DatasetValidationError('a batch must contain each target only once')

    @property
    def status(self) -> DatasetBatchStatus:
        """Resume fallos parciales y warnings sin ocultar publicaciones correctas."""

        if self.failures and not self.publications:
            return DatasetBatchStatus.FAILED
        if self.failures or any(
            result.quality is PublicationQuality.WARNING for result in self.publications
        ):
            return DatasetBatchStatus.WARNING
        return DatasetBatchStatus.SUCCESS

    @property
    def committed_count(self) -> int:
        return self._count_status(PublicationStatus.COMMITTED)

    @property
    def unchanged_count(self) -> int:
        return self._count_status(PublicationStatus.UNCHANGED)

    @property
    def skipped_count(self) -> int:
        return self._count_status(PublicationStatus.SKIPPED)

    @property
    def warning_count(self) -> int:
        return sum(result.warning_count for result in self.publications)

    @property
    def failed_count(self) -> int:
        return len(self.failures)

    def _count_status(self, status: PublicationStatus) -> int:
        return sum(result.status is status for result in self.publications)


def _validate_enum(value: object, enum_type: type[StrEnum], *, field: str) -> None:
    if not isinstance(value, enum_type):
        raise DatasetValidationError(f'{field} must be a {enum_type.__name__}')


def _validate_utc(value: datetime, *, field: str) -> None:
    if not isinstance(value, datetime):
        raise DatasetValidationError(f'{field} must be a datetime')
    if value.tzinfo is None or value.utcoffset() is None:
        raise DatasetValidationError(f'{field} must be timezone-aware')


def _validate_non_negative_integer(value: int, *, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DatasetValidationError(f'{field} must be a non-negative integer')


def _validate_non_negative_number(value: float, *, field: str) -> None:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise DatasetValidationError(f'{field} must be a non-negative number')
