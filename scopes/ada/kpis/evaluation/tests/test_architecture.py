from pathlib import Path


def test_evaluation_does_not_own_persistence_configuration_or_process_composition() -> None:
    source = '\n'.join(path.read_text() for path in Path('src').rglob('*.py'))
    forbidden = (
        'atlanticus.json',
        'atlanticus.state',
        'atlanticus.configuration',
        'atlanticus.observability',
        'cosmos',
        'azure',
        'ada.processes',
        'os.environ',
    )
    for token in forbidden:
        assert token not in source


def test_evaluation_has_no_direct_dataframe_dependency() -> None:
    source = '\n'.join(path.read_text() for path in Path('src').rglob('*.py'))
    assert 'pandas' not in source
    assert 'pyarrow' not in source


def test_evaluation_uses_shared_data_planner_and_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    source = '\n'.join(path.read_text() for path in (root / 'src').rglob('*.py'))
    pyproject = (root / 'pyproject.toml').read_text(encoding='utf-8')
    assert 'ada.data.planner' in source
    assert 'ada.data.sources' in source
    assert 'ada.kpis.planner' not in source
    assert 'ada.kpis.sources' not in source
    assert 'ada-operational-data-planner==0.1.0' in pyproject
    assert 'ada-operational-data-sources==0.1.0' in pyproject
