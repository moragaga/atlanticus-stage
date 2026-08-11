from __future__ import annotations

import tokenize
import unittest
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_PRODUCTION_ROOT = _PACKAGE_ROOT / 'src' / 'atlanticus' / 'kernel'
_COMMENTED_ROOT = _PACKAGE_ROOT / 'commented' / 'atlanticus' / 'kernel'

_IGNORED_TOKEN_TYPES = {
    tokenize.COMMENT,
    tokenize.ENCODING,
    tokenize.NL,
}


def _python_tokens(file_path: Path) -> list[tuple[int, str]]:
    with file_path.open('rb') as source:
        tokens = tokenize.tokenize(source.readline)
        return [
            (token.type, token.string) for token in tokens if token.type not in _IGNORED_TOKEN_TYPES
        ]


class CommentedMirrorTests(unittest.TestCase):
    def test_commented_mirror_contains_the_same_python_files(self) -> None:
        production_files = {path.name for path in _PRODUCTION_ROOT.glob('*.py')}
        commented_files = {path.name for path in _COMMENTED_ROOT.glob('*.py')}

        self.assertEqual(commented_files, production_files)

    def test_commented_mirror_only_adds_comments(self) -> None:
        for production_path in sorted(_PRODUCTION_ROOT.glob('*.py')):
            commented_path = _COMMENTED_ROOT / production_path.name

            with self.subTest(file=production_path.name):
                self.assertEqual(
                    _python_tokens(commented_path),
                    _python_tokens(production_path),
                )


if __name__ == '__main__':
    unittest.main()
