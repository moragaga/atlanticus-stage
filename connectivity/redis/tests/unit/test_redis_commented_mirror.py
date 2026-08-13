from __future__ import annotations

import io
import tokenize
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PRODUCT = _ROOT / 'src/atlanticus/connectivity/redis'
_COMMENTED = _ROOT / 'commented/atlanticus/connectivity/redis'
_FILES = ('__init__.py', 'client.py', 'errors.py', 'models.py', 'settings.py')
_IGNORED = {tokenize.COMMENT, tokenize.ENCODING, tokenize.NL}


def _tokens(path: Path) -> list[tuple[int, str]]:
    source = path.read_bytes()
    return [
        (token.type, token.string)
        for token in tokenize.tokenize(io.BytesIO(source).readline)
        if token.type not in _IGNORED
    ]


def test_commented_mirror_has_same_python_file_shape() -> None:
    assert {path.name for path in _COMMENTED.glob('*.py')} == set(_FILES)


def test_commented_mirror_differs_only_by_comments() -> None:
    for name in _FILES:
        assert _tokens(_PRODUCT / name) == _tokens(_COMMENTED / name), name
