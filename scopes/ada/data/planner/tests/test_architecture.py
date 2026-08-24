from pathlib import Path


def test_planner_only_depends_on_shared_data_core() -> None:
    source = '\n'.join(path.read_text() for path in Path('src').rglob('*.py'))
    forbidden = ('atlanticus.', 'pandas', 'pyarrow', 'cosmos', 'azure', 'ada.kpis', 'ada.alarms')
    for token in forbidden:
        assert token not in source
