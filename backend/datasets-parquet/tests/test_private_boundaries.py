from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_ROOT = _PACKAGE_ROOT.parents[1]
_SOURCE_ROOTS = (
    _REPOSITORY_ROOT / 'backend',
    _REPOSITORY_ROOT / 'connectivity',
    _REPOSITORY_ROOT / 'integrations',
    _REPOSITORY_ROOT / 'scopes',
)
_PRIVATE_PREFIX = 'atlanticus.datasets.parquet._'


def test_external_sources_do_not_import_parquet_private_modules() -> None:
    violations: list[str] = []
    for source_root in _SOURCE_ROOTS:
        if not source_root.exists():
            continue
        for path in source_root.rglob('src/**/*.py'):
            if path.is_relative_to(_PACKAGE_ROOT):
                continue
            for line_number, line in enumerate(
                path.read_text(encoding='utf-8').splitlines(),
                start=1,
            ):
                if _PRIVATE_PREFIX in line:
                    relative = path.relative_to(_REPOSITORY_ROOT)
                    violations.append(f'{relative}:{line_number}: {line.strip()}')
    assert not violations, 'external code references private parquet modules:\n' + '\n'.join(
        violations
    )
