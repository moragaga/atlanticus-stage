from __future__ import annotations

import pytest

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
