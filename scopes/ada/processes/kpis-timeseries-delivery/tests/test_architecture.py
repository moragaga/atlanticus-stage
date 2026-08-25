import ast
from pathlib import Path


def test_timeseries_does_not_depend_on_historian_process_package() -> None:
    source = '\n'.join(path.read_text(encoding='utf-8') for path in Path('src').rglob('*.py'))

    assert 'ada.processes.kpis_historian' not in source
    assert 'error-history' not in source


def test_history_reader_projects_dataset_without_kpi_status_semantics() -> None:
    source = Path('src/ada/processes/kpis_timeseries_delivery/history.py').read_text(
        encoding='utf-8'
    )

    assert "columns=('timestamp_utc', 'key', 'value')" in source
    assert "record.get('status')" not in source
    assert 'nearest' not in source.lower()
    assert 'interpol' not in source.lower()


def test_cosmos_client_is_created_only_inside_explicit_composition() -> None:
    for path in Path('src').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        source = path.read_text(encoding='utf-8')
        if path.name == 'composition.py':
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign | ast.AnnAssign):
                text = ast.get_source_segment(source, node) or ''
                assert 'CosmosClient(' not in text

    composition = Path('src/ada/processes/kpis_timeseries_delivery/composition.py').read_text(
        encoding='utf-8'
    )
    assert 'cosmos_client = CosmosClient(settings=settings.cosmos)' in composition


def test_job_has_only_the_agreed_internal_step() -> None:
    source = Path('src/ada/processes/kpis_timeseries_delivery/job.py').read_text(encoding='utf-8')

    assert 'KPI_TIMESERIES_STEP_SECONDS = 120' in source
    assert 'CosmosClient' not in source
    assert 'series_hours' in source
    assert 'destination_keys' not in source
