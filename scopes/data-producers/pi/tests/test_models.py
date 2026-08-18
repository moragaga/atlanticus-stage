from atlanticus.data_producers.pi import PiExecutionPlan, ResolvedPiTag
from atlanticus.integrations.pi.contracts import (
    PiExtractionMode,
    PiMaterialization,
    PiTagDefinition,
    PiValueKind,
)


def _definition(name: str, alias: str, mode: PiExtractionMode) -> PiTagDefinition:
    materializations = (
        (PiMaterialization.DAILY,)
        if mode is PiExtractionMode.RECORDED
        else (PiMaterialization.LATEST, PiMaterialization.DAILY)
    )
    return PiTagDefinition(
        tag_name=name,
        alias=alias,
        value_kind=PiValueKind.NUMBER,
        extraction_mode=mode,
        materializations=materializations,
    )


def test_execution_plan_indexes_resolved_tags_by_real_pi_name() -> None:
    a = ResolvedPiTag(
        definition=_definition('TAG_A', 'a', PiExtractionMode.INTERPOLATED),
        web_id='WEB_A',
    )
    b = ResolvedPiTag(
        definition=_definition('TAG_B', 'b', PiExtractionMode.RECORDED),
        web_id='WEB_B',
    )
    plan = PiExecutionPlan(interpolated=(a,), recorded=(b,))

    assert plan.by_name == {'TAG_A': a, 'TAG_B': b}
    assert a.alias == 'a'
    assert b.extraction_mode is PiExtractionMode.RECORDED
