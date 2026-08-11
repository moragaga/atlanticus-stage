from __future__ import annotations

import io
import tokenize
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_ROOT = _PACKAGE_ROOT / 'src' / 'atlanticus' / 'runtime'
_COMMENTED_ROOT = _PACKAGE_ROOT / 'commented' / 'atlanticus' / 'runtime'


def _python_tokens(path: Path) -> list[tuple[int, str]]:
    source = path.read_text(encoding='utf-8')
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
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type not in ignored
    ]


def test_runtime_commented_mirror_contains_the_same_python_files() -> None:
    production_files = {path.name for path in _PRODUCTION_ROOT.glob('*.py')}
    commented_files = {path.name for path in _COMMENTED_ROOT.glob('*.py')}
    assert commented_files == production_files


def test_runtime_commented_mirror_only_adds_comments() -> None:
    for production_path in _PRODUCTION_ROOT.glob('*.py'):
        commented_path = _COMMENTED_ROOT / production_path.name
        assert _python_tokens(commented_path) == _python_tokens(production_path)
