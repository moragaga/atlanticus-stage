from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import atlanticus.integrations.pi.web_api as web_api

_PACKAGE_ROOT = Path(__file__).parents[2]
_SOURCE_ROOT = _PACKAGE_ROOT / 'src' / 'atlanticus' / 'integrations' / 'pi' / 'web_api'
_COMMENTED_ROOT = _PACKAGE_ROOT / 'commented' / 'atlanticus' / 'integrations' / 'pi' / 'web_api'


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
    assert web_api.__all__ == [
        'PiPointWebIdResult',
        'PiWebApiClient',
        'PiWebApiConfigurationError',
        'PiWebApiConnectionError',
        'PiWebApiError',
        'PiWebApiLimits',
        'PiWebApiRequestError',
        'PiWebApiResponseError',
        'PiWebApiSettings',
        'PiWebApiStatusError',
        'PiWebApiTimeoutError',
    ]


def test_second_increment_keeps_resources_behind_client() -> None:
    settings = web_api.PiWebApiSettings
    assert 'streamsets' not in settings.__dataclass_fields__
    assert not hasattr(web_api, 'PiStreamResource')
    assert not hasattr(web_api, 'PiStreamSetResource')


def test_web_api_has_no_process_or_dataframe_dependencies() -> None:
    forbidden_roots = {
        'azure',
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


def test_web_api_does_not_own_process_policy() -> None:
    source = '\n'.join(path.read_text().lower() for path in _SOURCE_ROOT.glob('*.py'))

    for forbidden in ('retry', 'backfill', 'watermark', 'materialization', 'dataframe'):
        assert forbidden not in source


def test_commented_mirror_only_adds_comments() -> None:
    production_paths = sorted(path for path in _SOURCE_ROOT.glob('*.py') if path.name != 'py.typed')
    assert production_paths

    for production_path in production_paths:
        commented_path = _COMMENTED_ROOT / production_path.name
        assert commented_path.exists()
        assert _python_tokens(commented_path) == _python_tokens(production_path)
