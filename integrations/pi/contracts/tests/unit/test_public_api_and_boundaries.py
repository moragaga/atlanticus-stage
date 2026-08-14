from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import atlanticus.integrations.pi.contracts as contracts

_PACKAGE_ROOT = Path(__file__).parents[2]
_SOURCE_ROOT = _PACKAGE_ROOT / 'src' / 'atlanticus' / 'integrations' / 'pi' / 'contracts'
_COMMENTED_ROOT = _PACKAGE_ROOT / 'commented' / 'atlanticus' / 'integrations' / 'pi' / 'contracts'


def _python_tokens(path: Path) -> list[tuple[int, str]]:
    ignored = {
        tokenize.COMMENT,
        tokenize.ENCODING,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    return [
        (token.type, token.string)
        for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
        if token.type not in ignored
    ]


def test_public_api_is_explicit() -> None:
    assert contracts.__all__ == [
        'NotPiiSource',
        'PiCatalog',
        'PiExtractionMode',
        'PiMaterialization',
        'PiSource',
        'PiTagDefinition',
        'PiValueKind',
        'PiWebApiSource',
    ]


def test_tag_definition_contains_only_tag_and_materialization_contract() -> None:
    fields = set(contracts.PiTagDefinition.__dataclass_fields__)

    assert fields == {
        'tag_name',
        'alias',
        'value_kind',
        'extraction_mode',
        'materializations',
        'is_active',
    }
    assert fields.isdisjoint(
        {
            'key',
            'tag_key',
            'interpolation_seconds',
            'web_id',
            'pi_server',
            'endpoint',
            'notpii_topic',
            'storage_path',
        }
    )


def test_source_contracts_do_not_repeat_tag_identity() -> None:
    assert set(contracts.NotPiiSource.__dataclass_fields__) == set()
    assert set(contracts.PiWebApiSource.__dataclass_fields__) == {'interpolation_seconds'}


def test_legacy_source_binding_is_not_public_or_defined() -> None:
    assert not hasattr(contracts, 'PiSourceBinding')


def test_contracts_have_no_external_runtime_dependencies() -> None:
    forbidden_roots = {
        'ada',
        'azure',
        'httpx',
        'pandas',
        'polars',
        'pyarrow',
        'redis',
        'requests',
    }

    imported_roots: set[str] = set()
    for path in _SOURCE_ROOT.glob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported_roots.add(node.module.split('.')[0])

    assert imported_roots.isdisjoint(forbidden_roots)


def test_contracts_do_not_contain_connection_or_scope_configuration() -> None:
    source = '\n'.join(path.read_text().lower() for path in _SOURCE_ROOT.glob('*.py'))

    for forbidden in (
        'ada_',
        'mlp_',
        'amsa_',
        'webid',
        'web_id',
        'pi_server',
        'endpoint',
        'connection_string',
        'service_bus',
        'storage_path',
        'watermark',
    ):
        assert forbidden not in source


def test_commented_mirror_only_adds_comments() -> None:
    production_paths = sorted(path for path in _SOURCE_ROOT.glob('*.py') if path.name != 'py.typed')
    assert production_paths

    for production_path in production_paths:
        commented_path = _COMMENTED_ROOT / production_path.name
        assert commented_path.exists()
        assert _python_tokens(commented_path) == _python_tokens(production_path)
