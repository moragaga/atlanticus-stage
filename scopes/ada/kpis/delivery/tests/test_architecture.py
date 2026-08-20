from pathlib import Path


def test_delivery_projection_has_no_process_or_infrastructure_dependencies() -> None:
    source = '\n'.join(path.read_text(encoding='utf-8') for path in Path('src').rglob('*.py'))
    forbidden = (
        'ada.processes',
        'atlanticus.configuration',
        'atlanticus.connectivity',
        'atlanticus.runtime',
        'azure',
        'cosmos',
        'os.environ',
    )
    for token in forbidden:
        assert token not in source
