from __future__ import annotations

import tokenize
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_PRODUCTION_ROOT = _PACKAGE_ROOT / 'src' / 'atlanticus' / 'connectivity' / 'http'
_COMMENTED_ROOT = _PACKAGE_ROOT / 'commented' / 'atlanticus' / 'connectivity' / 'http'
_IGNORED_TOKEN_TYPES = {tokenize.COMMENT, tokenize.ENCODING, tokenize.NL}


def _python_tokens(file_path: Path) -> list[tuple[int, str]]:
    with file_path.open('rb') as source:
        return [
            (token.type, token.string)
            for token in tokenize.tokenize(source.readline)
            if token.type not in _IGNORED_TOKEN_TYPES
        ]


def test_commented_mirror_contains_the_same_python_files() -> None:
    production_files = {path.name for path in _PRODUCTION_ROOT.glob('*.py')}
    commented_files = {path.name for path in _COMMENTED_ROOT.glob('*.py')}
    assert commented_files == production_files


def test_commented_mirror_only_adds_comments() -> None:
    for production_path in sorted(_PRODUCTION_ROOT.glob('*.py')):
        commented_path = _COMMENTED_ROOT / production_path.name
        assert _python_tokens(commented_path) == _python_tokens(production_path)
