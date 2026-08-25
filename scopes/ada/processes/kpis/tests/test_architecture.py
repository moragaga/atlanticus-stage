import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / 'src' / 'ada' / 'processes' / 'kpis'


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding='utf-8'))
    output = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            output.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            output.append(node.module)
    return tuple(output)


def test_catalog_base_contains_only_general_and_shared_scaffold() -> None:
    catalog = _SRC / 'catalog'
    assert (catalog / 'general' / 'specs.py').is_file()
    assert (catalog / 'general' / 'resolvers.py').is_file()
    assert (catalog / 'general' / 'logics' / '.gitkeep').is_file()
    assert (catalog / 'general' / 'over' / 'specs.py').is_file()
    assert (catalog / 'general' / 'over' / 'resolvers.py').is_file()
    assert (catalog / 'general' / 'over' / 'logics' / '.gitkeep').is_file()
    assert (catalog / 'shared' / 'logics' / '.gitkeep').is_file()
    assert not (catalog / 'mina').exists()
    assert not (catalog / 'planta').exists()


def test_process_does_not_import_concrete_pi_or_notpii_data_producer_packages() -> None:
    imports = tuple(imported for path in _SRC.rglob('*.py') for imported in _imports(path))
    assert not any(imported.startswith('atlanticus.data_producers.pi') for imported in imports)
    assert not any(imported.startswith('atlanticus.data_producers.notpii') for imported in imports)


def test_shared_logics_is_not_used_as_a_catalog_or_resolver_layer() -> None:
    registry_imports = _imports(_SRC / 'catalog' / 'registry.py')
    assert not any('.shared.logics' in imported for imported in registry_imports)


def test_process_uses_shared_data_ownership_without_legacy_kpi_planner_or_sources() -> None:
    source = '\n'.join(path.read_text(encoding='utf-8') for path in _SRC.rglob('*.py'))
    pyproject = (_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert 'ada.kpis.planner' not in source
    assert 'ada.kpis.sources' not in source
    assert 'ada-kpis-planner' not in pyproject
    assert 'ada-kpis-sources' not in pyproject
    assert 'ada-operational-data-core==0.1.0' in pyproject
    assert 'ada-operational-data-sources==0.1.0' in pyproject
