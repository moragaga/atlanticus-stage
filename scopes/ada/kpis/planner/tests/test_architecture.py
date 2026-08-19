from pathlib import Path


def test_planner_does_not_import_runtime_or_infrastructure() -> None:
    source = '\n'.join(path.read_text() for path in Path('src').rglob('*.py'))
    forbidden = (
        'atlanticus.',
        'pandas',
        'pyarrow',
        'cosmos',
        'azure',
        'ada.processes',
    )
    for token in forbidden:
        assert token not in source
