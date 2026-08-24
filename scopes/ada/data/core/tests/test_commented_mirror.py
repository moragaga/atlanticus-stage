from __future__ import annotations

import io
import tokenize
from pathlib import Path

_PRODUCTION_ROOT = Path('src/ada/data/core')
_COMMENTED_ROOT = Path('commented/ada/data/core')


def _python_tokens(path: Path) -> list[tuple[int, str]]:
    ignored = {
        tokenize.ENCODING,
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    return [
        (token.type, token.string)
        for token in tokenize.generate_tokens(
            io.StringIO(path.read_text(encoding='utf-8')).readline
        )
        if token.type not in ignored
    ]


def test_commented_mirror_only_adds_comments() -> None:
    for production_path in sorted(_PRODUCTION_ROOT.glob('*.py')):
        commented_path = _COMMENTED_ROOT / production_path.name
        assert commented_path.exists()
        assert _python_tokens(commented_path) == _python_tokens(production_path)
