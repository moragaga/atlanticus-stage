import io
import tokenize
from pathlib import Path

_PRODUCTION_ROOT = Path('src/ada/processes/kpis_timeseries_delivery')
_COMMENTED_ROOT = Path('commented/ada/processes/kpis_timeseries_delivery')


def _tokens(path: Path) -> list[tuple[int, str]]:
    ignored = {
        tokenize.COMMENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NEWLINE,
        tokenize.NL,
    }
    result: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
        if token.type not in ignored:
            result.append((token.type, token.string))
    return result


def test_commented_mirror_only_adds_comments() -> None:
    production = {path.name for path in _PRODUCTION_ROOT.glob('*.py')}
    commented = {path.name for path in _COMMENTED_ROOT.glob('*.py')}

    assert production == commented
    for name in production:
        assert _tokens(_PRODUCTION_ROOT / name) == _tokens(_COMMENTED_ROOT / name)
