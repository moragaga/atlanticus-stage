import pytest

from ada.processes.kpis_historian.errors import KpiHistorianWatermarkError
from ada.processes.kpis_historian.state import KpiHistorianCommitStore
from atlanticus.state import AtomicStateStore
from tests.support import watermark


def test_historian_state_is_isolated_under_its_own_namespace(tmp_path) -> None:
    store = AtomicStateStore(volume_path=tmp_path, application='ada-operaciones-integradas-local')
    state = KpiHistorianCommitStore(store=store)
    target = watermark(19, 10)

    state.commit_watermark(target)

    assert state.read_watermark() == target
    assert (
        tmp_path
        / 'ada-operaciones-integradas-local'
        / '.runtime'
        / 'state'
        / 'kpis-historian'
        / 'committed-watermark.json'
    ).is_file()


def test_historian_state_rejects_regression(tmp_path) -> None:
    store = AtomicStateStore(volume_path=tmp_path, application='ada-operaciones-integradas-local')
    state = KpiHistorianCommitStore(store=store)
    state.commit_watermark(watermark(19, 20))

    with pytest.raises(KpiHistorianWatermarkError, match='must not move backwards'):
        state.commit_watermark(watermark(19, 10))
