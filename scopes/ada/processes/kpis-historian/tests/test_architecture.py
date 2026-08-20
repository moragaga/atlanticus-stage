import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / 'src' / 'ada' / 'processes' / 'kpis_historian'


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    output = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            output.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            output.append(node.module)
    return tuple(output)


def test_historian_does_not_import_kpi_process_or_concrete_data_producers() -> None:
    imports = tuple(imported for path in _SRC.rglob('*.py') for imported in _imports(path))
    assert not any(imported.startswith('ada.processes.kpis.') for imported in imports)
    assert not any(imported.startswith('atlanticus.data_producers.') for imported in imports)


def test_historian_has_no_cross_application_routing_contract() -> None:
    source = (_SRC / 'settings.py').read_text(encoding='utf-8')
    assert 'KPI_APPLICATION' not in source
    assert 'HISTORIAN_APPLICATION' not in source
