import io
import tokenize
from pathlib import Path

_PRODUCTION_ROOT = Path('src/ada/kpis/evaluation')
_COMMENTED_ROOT = Path('commented/ada/kpis/evaluation')


def _python_tokens(path: Path) -> list[tuple[int, str]]:
    tokens: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
        if token.type in {
            tokenize.COMMENT,
            tokenize.ENCODING,
            tokenize.ENDMARKER,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
        }:
            continue
        tokens.append((token.type, token.string))
    return tokens


def test_commented_mirror_only_adds_comments() -> None:
    for production_path in sorted(_PRODUCTION_ROOT.glob('*.py')):
        commented_path = _COMMENTED_ROOT / production_path.name
        assert commented_path.exists()
        assert _python_tokens(commented_path) == _python_tokens(production_path)
