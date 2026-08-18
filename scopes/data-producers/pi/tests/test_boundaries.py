from pathlib import Path


def test_pi_data_producer_has_no_ada_dependency() -> None:
    root = Path(__file__).resolve().parents[1] / 'src' / 'atlanticus' / 'data_producers' / 'pi'

    for path in root.rglob('*.py'):
        source = path.read_text(encoding='utf-8')
        assert 'from ada.' not in source, path
        assert 'import ada.' not in source, path
