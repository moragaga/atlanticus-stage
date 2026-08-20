from pathlib import Path


def test_process_repository_uses_existing_cosmos_connectivity_boundary() -> None:
    source = Path('src/ada/processes/kpis_delivery/repository.py').read_text(encoding='utf-8')

    assert 'from atlanticus.connectivity.cosmos import CosmosClient' in source
    assert 'azure.cosmos' not in source
    assert 'CosmosSettings' not in source
    assert 'CosmosProvisioner' not in source
    assert 'os.environ' not in source


def test_process_does_not_create_global_cosmos_client() -> None:
    source = '\n'.join(path.read_text(encoding='utf-8') for path in Path('src').rglob('*.py'))

    assert 'CosmosClient(' not in source
