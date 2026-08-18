from pathlib import Path


def test_notpii_data_producer_has_no_ada_imports() -> None:
    root = Path(__file__).resolve().parents[1] / 'src' / 'atlanticus' / 'data_producers' / 'notpii'
    for path in root.rglob('*.py'):
        text = path.read_text(encoding='utf-8')
        assert 'from ada.' not in text, path
        assert 'import ada.' not in text, path
