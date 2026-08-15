from __future__ import annotations

from pathlib import Path

import pytest

from atlanticus.configuration import (
    ConfigurationBootstrap,
    ConfigurationVariableSpec,
    ResolvedConfiguration,
)
from atlanticus.integrations.pi.contracts import (
    PiCatalog,
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
    PiWebApiSource,
)


@pytest.fixture
def catalog() -> PiCatalog:
    return PiCatalog(
        source=PiWebApiSource(interpolation_seconds=10),
        definitions=(
            PiTagDefinition(
                tag_name='TAG_A',
                alias='a',
                value_kind=PiValueKind.NUMBER,
                extraction_mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.LATEST, PiMaterialization.DAILY),
            ),
            PiTagDefinition(
                tag_name='TAG_B',
                alias='b',
                value_kind=PiValueKind.TEXT,
                extraction_mode=PiExtractionMode.RECORDED,
                materializations=(PiMaterialization.DAILY,),
            ),
            PiTagDefinition(
                tag_name='TAG_DISABLED',
                alias='disabled',
                value_kind=PiValueKind.NUMBER,
                extraction_mode=PiExtractionMode.INTERPOLATED,
                materializations=(PiMaterialization.DAILY,),
                is_active=False,
            ),
        ),
    )


@pytest.fixture
def configuration(tmp_path: Path) -> ResolvedConfiguration:
    values = {
        'ENVIRONMENT': 'local',
        'APPLICATION': 'ada',
        'VOLUMEN_PATH': str(tmp_path),
        'PI_WEB_API_BASE_URL': 'https://pi.example.local/piwebapi/',
        'PI_WEB_API_SERVER': 'PISERVER',
        'PI_WEB_API_USERNAME': 'domain\\user',
        'PI_WEB_API_PASSWORD': 'secret',
        'PI_WEB_API_CONNECT_TIMEOUT_SECONDS': '5',
        'PI_WEB_API_READ_TIMEOUT_SECONDS': '30',
        'PI_WEB_API_WRITE_TIMEOUT_SECONDS': '30',
        'PI_WEB_API_POOL_TIMEOUT_SECONDS': '5',
        'PI_WEB_API_MAX_RESPONSE_BYTES': '67108864',
        'PI_WEB_API_VERIFY_TLS': 'true',
        'PI_WEB_API_ALLOW_INSECURE_HTTP': 'false',
        'PI_WEB_API_POINTS_MAX_PATHS': '100',
        'PI_WEB_API_INTERPOLATED_MAX_WEB_IDS': '200',
        'PI_WEB_API_RECORDED_MAX_WEB_IDS': '100',
        'PI_WEB_API_MAX_RECOVERY_LOOKBACK_SECONDS': '3600',
        'PI_WEB_API_MAX_RECOVERY_WINDOW_SECONDS': '3600',
        'PI_WEB_API_INTERPOLATED_MAX_PARALLEL_REQUESTS': '3',
        'PI_WEB_API_MAX_DATA_POINTS': '150000',
        'ATLANTICUS_AZURE_OBSERVABILITY_MODE': 'off',
    }
    specs = tuple(
        ConfigurationVariableSpec(key=key, sensitive=key.endswith(('USERNAME', 'PASSWORD')))
        for key in values
        if key != 'ENVIRONMENT'
    )
    return ConfigurationBootstrap.from_process(specs=specs, process_values=values).load(
        process_values=values
    )
