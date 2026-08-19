from pathlib import Path


def test_sources_does_not_import_process_or_data_producer_implementations() -> None:
    source = '\n'.join(path.read_text() for path in Path('src').rglob('*.py'))
    forbidden = (
        'ada.processes',
        'atlanticus.data_producers',
        'atlanticus.connectivity',
        'cosmos',
        'azure.',
    )
    for token in forbidden:
        assert token not in source


def test_sources_imports_dataset_contracts_from_concrete_modules() -> None:
    source = '\n'.join(path.read_text() for path in Path('src').rglob('*.py'))

    assert 'from atlanticus.datasets import' not in source
