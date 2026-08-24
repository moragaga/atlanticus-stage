import io
import token
import tokenize
from pathlib import Path

_PRODUCTION_ROOT = Path(__file__).parents[1] / 'src' / 'ada' / 'processes' / 'alarms_runtime'
_COMMENTED_ROOT = Path(__file__).parents[1] / 'commented' / 'ada' / 'processes' / 'alarms_runtime'


def _python_tokens(path: Path) -> list[tuple[int, str]]:
    ignored = {
        token.COMMENT,
        token.ENCODING,
        token.ENDMARKER,
        token.INDENT,
        token.DEDENT,
        token.NEWLINE,
        tokenize.NL,
    }
    source = path.read_bytes()
    return [
        (item.type, item.string)
        for item in tokenize.tokenize(io.BytesIO(source).readline)
        if item.type not in ignored
    ]


def test_commented_mirror_only_adds_comments() -> None:
    for production_path in sorted(_PRODUCTION_ROOT.glob('*.py')):
        commented_path = _COMMENTED_ROOT / production_path.name
        assert commented_path.exists()
        assert _python_tokens(commented_path) == _python_tokens(production_path)
