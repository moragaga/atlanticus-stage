# Resuelve únicamente las rutas canónicas bajo el root de la aplicación.
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ada.kpis.core import KpiWatermark
from ada.kpis.persistence.errors import KpiPersistenceValidationError


@dataclass(frozen=True, slots=True)
class KpiPersistencePaths:
    application_root: Path

    def __post_init__(self) -> None:
        value = _absolute_path(self.application_root)
        object.__setattr__(self, 'application_root', value)

    @property
    def datasets_root(self) -> Path:
        return self.application_root / 'datasets' / 'kpis'

    @property
    def evaluations_root(self) -> Path:
        return self.datasets_root / 'evaluations'

    @property
    def latest_path(self) -> Path:
        return self.datasets_root / 'latest' / 'data.json'

    def evaluation_path(self, watermark: KpiWatermark) -> Path:
        if not isinstance(watermark, KpiWatermark):
            raise TypeError('watermark must be KpiWatermark')
        timestamp = watermark.timestamp_utc
        return (
            self.evaluations_root
            / f'year={timestamp.year:04d}'
            / f'month={timestamp.month:02d}'
            / f'day={timestamp.day:02d}'
            / f'{watermark.filename_token}.json'
        )


def _absolute_path(value: object) -> Path:
    if not isinstance(value, str | Path):
        raise KpiPersistenceValidationError('application_root must be a filesystem path')
    if isinstance(value, str) and not value.strip():
        raise KpiPersistenceValidationError('application_root must not be empty')
    path = Path(value)
    if not path.is_absolute():
        raise KpiPersistenceValidationError('application_root must be absolute')
    return path
