import io
import tokenize
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_ROOT = _ROOT / 'src' / 'ada' / 'processes' / 'kpis'
_COMMENTED_ROOT = _ROOT / 'commented' / 'ada' / 'processes' / 'kpis'


def _python_tokens(path: Path):
    content = path.read_bytes()
    ignored = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
    return [
        (token.type, token.string)
        for token in tokenize.tokenize(io.BytesIO(content).readline)
        if token.type not in ignored
    ]


def test_commented_mirror_only_adds_comments() -> None:
    production_paths = tuple(sorted(_PRODUCTION_ROOT.rglob('*.py')))
    assert production_paths
    for production_path in production_paths:
        relative = production_path.relative_to(_PRODUCTION_ROOT)
        commented_path = _COMMENTED_ROOT / relative
        assert commented_path.is_file(), relative
        assert _python_tokens(commented_path) == _python_tokens(production_path)
