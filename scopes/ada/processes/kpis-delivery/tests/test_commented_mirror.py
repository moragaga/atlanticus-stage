import io
import tokenize
from pathlib import Path

_PRODUCTION_ROOT = Path('src/ada/processes/kpis_delivery')
_COMMENTED_ROOT = Path('commented/ada/processes/kpis_delivery')


def _python_tokens(path: Path) -> list[tuple[int, str]]:
    ignored = {
        tokenize.COMMENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NEWLINE,
        tokenize.NL,
    }
    tokens: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
        if token.type not in ignored:
            tokens.append((token.type, token.string))
    return tokens


def test_commented_mirror_only_adds_comments() -> None:
    production = {path.name for path in _PRODUCTION_ROOT.glob('*.py')}
    commented = {path.name for path in _COMMENTED_ROOT.glob('*.py')}

    assert production == commented
    for name in sorted(production):
        assert _python_tokens(_COMMENTED_ROOT / name) == _python_tokens(_PRODUCTION_ROOT / name)
