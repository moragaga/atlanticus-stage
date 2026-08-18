import re
from types import SimpleNamespace

from atlanticus.data_producers.fabrica import (
    FabricaJob,
    FabricaKpiStreamDefinition,
    FabricaMaterializer,
    FabricaProducerState,
)
from atlanticus.state import AtomicStateStore


class _EmptyCatalogMaterializer(FabricaMaterializer):
    def __init__(self) -> None:
        self.definition = FabricaKpiStreamDefinition(
            source_prefix='MLP/kpi_fabrica/kpi_fabrica',
            source_filename_pattern=re.compile(r'kpi_fabrica_(?P<file_timestamp>\d{14})\.parquet$'),
            output_route_segment='kpis',
            datasets=(),
        )
        self.latest_calls = 0

    def latest_source(self, *, prefix: str):
        self.latest_calls += 1
        raise AssertionError(f'latest_source must not be called for empty catalog: {prefix}')


class _Logger:
    def __init__(self) -> None:
        self.debug_events: list[tuple[str, dict[str, object]]] = []

    def debug(self, message: str, **fields: object) -> None:
        self.debug_events.append((message, fields))


class _Context:
    def __init__(self) -> None:
        self.configuration = SimpleNamespace(environment=SimpleNamespace(is_local=True))
        self.logger = _Logger()
        self.iteration_has_work = False
        self.execution_facts: dict[str, object] = {}
        self.iteration_facts: dict[str, object] = {}

    def mark_iteration_work(self) -> None:
        self.iteration_has_work = True

    def get_execution_fact(self, key: str):
        return self.execution_facts.get(key)

    def set_execution_fact(self, key: str, value: object) -> None:
        self.execution_facts[key] = value

    def increment_execution_counter(self, key: str, value: int = 1) -> None:
        self.execution_facts[key] = int(self.execution_facts.get(key, 0)) + value

    def set_iteration_fact(self, key: str, value: object) -> None:
        self.iteration_facts[key] = value

    def wait(self, seconds: float) -> None:
        raise AssertionError(f'local empty catalog must not wait: {seconds}')


def test_empty_catalog_stream_is_disabled_without_source_access(tmp_path) -> None:
    materializer = _EmptyCatalogMaterializer()
    state = FabricaProducerState(store=AtomicStateStore(volume_path=tmp_path, application='ada'))
    context = _Context()
    job = FabricaJob(materializers=(materializer,), producer_state=state, idle_seconds=5)

    job.run_iteration(context)

    assert materializer.latest_calls == 0
    assert context.iteration_facts['streams_planned'] == 0
    assert context.logger.debug_events == [
        (
            'Stream disabled',
            {
                'event_name': 'fabrica.stream.disabled',
                'stream': 'kpis',
                'reason': 'empty_catalog',
            },
        )
    ]
