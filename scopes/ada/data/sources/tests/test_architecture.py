from pathlib import Path


def test_sources_depend_on_shared_data_layers_not_kpi_or_alarm_domains() -> None:
    source = '\n'.join(path.read_text() for path in Path('src').rglob('*.py'))
    assert 'ada.kpis' not in source
    assert 'ada.alarms' not in source
    assert 'atlanticus.datasets.runtime' not in source
