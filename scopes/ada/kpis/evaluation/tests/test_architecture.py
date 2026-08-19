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
