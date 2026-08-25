import ast
from pathlib import Path


def test_process_adapters_use_existing_cosmos_boundary() -> None:
    for name in ('configuration.py', 'repository.py'):
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
        if path.name == 'composition.py':
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign | ast.AnnAssign):
                text = ast.get_source_segment(source, node) or ''
                assert 'CosmosClient(' not in text

    composition = Path('src/ada/processes/kpis_delivery/composition.py').read_text(encoding='utf-8')
    assert 'cosmos_client = CosmosClient(settings=settings.cosmos)' in composition


def test_job_depends_on_contracts_not_cosmos_or_environment_shape() -> None:
    source = Path('src/ada/processes/kpis_delivery/job.py').read_text(encoding='utf-8')

    assert 'CosmosClient' not in source
    assert 'CosmosSettings' not in source
    assert 'destination_keys' not in source
    assert 'latest_enabled' not in source
    assert 'os.environ' not in source


def test_configuration_projection_shape_is_owned_by_delivery_contract() -> None:
    process_source = '\n'.join(
        path.read_text(encoding='utf-8')
        for path in Path('src/ada/processes/kpis_delivery').glob('*.py')
    )

    assert "'ada_kpi_configuration_projection'" not in process_source
    assert "'destination_keys'" not in process_source
    assert "'series_hours'" not in process_source
