import ast
from pathlib import Path


def test_process_repositories_use_existing_cosmos_connectivity_boundary() -> None:
    for name in ('bindings.py', 'repository.py'):
        source = Path(f'src/ada/processes/kpis_delivery/{name}').read_text(encoding='utf-8')

        assert 'from atlanticus.connectivity.cosmos import CosmosClient' in source
        assert 'azure.cosmos' not in source
        assert 'CosmosSettings' not in source
        assert 'CosmosProvisioner' not in source
        assert 'os.environ' not in source


def test_cosmos_client_is_created_only_inside_explicit_composition() -> None:
    for path in Path('src').rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        source = path.read_text(encoding='utf-8')
        for node in tree.body:
            if isinstance(node, ast.Assign | ast.AnnAssign):
                text = ast.get_source_segment(source, node) or ''
                assert 'CosmosClient(' not in text

    composition = Path('src/ada/processes/kpis_delivery/composition.py').read_text(encoding='utf-8')
    assert 'cosmos_client = CosmosClient(settings=settings.cosmos)' in composition


def test_job_does_not_depend_on_configuration_or_cosmos_shape() -> None:
    source = Path('src/ada/processes/kpis_delivery/job.py').read_text(encoding='utf-8')

    assert 'CosmosClient' not in source
    assert 'CosmosSettings' not in source
    assert "'kind'" not in source
    assert 'configuration' not in source
    assert 'os.environ' not in source


def test_configuration_snapshot_shape_is_isolated_to_bindings_adapter() -> None:
    adapter = Path('src/ada/processes/kpis_delivery/bindings.py').read_text(encoding='utf-8')
    other_source = '\n'.join(
        path.read_text(encoding='utf-8')
        for path in Path('src/ada/processes/kpis_delivery').glob('*.py')
        if path.name != 'bindings.py'
    )

    assert "_CONFIGURATION_PARTITION_ID = 'configuration'" in adapter
    assert "_KPI_KIND = 'kpi'" in adapter
    assert "entry.get('key')" in adapter
    assert "entry.get('kind')" in adapter
    assert "entry.get('kind')" not in other_source
