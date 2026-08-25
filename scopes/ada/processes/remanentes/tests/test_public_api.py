from __future__ import annotations

import ada.processes.remanentes as process


def test_public_api_exposes_only_process_entrypoints() -> None:
    assert set(process.__all__) == {'__version__', 'build_catalog', 'build_composition', 'run'}
    assert process.__version__ == '0.1.1'
