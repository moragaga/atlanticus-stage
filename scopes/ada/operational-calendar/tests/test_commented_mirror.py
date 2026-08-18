from __future__ import annotations

import ast
from pathlib import Path


def test_productive_and_commented_sources_are_equivalent() -> None:
    root = Path(__file__).resolve().parents[1]
    source_root = root / 'src'
    commented_root = root / 'commented'
    sources = tuple(sorted(source_root.rglob('*.py')))
    assert sources
    for source in sources:
        commented = commented_root / source.relative_to(source_root)
        assert commented.is_file()
        assert ast.dump(ast.parse(source.read_text(encoding='utf-8'))) == ast.dump(
            ast.parse(commented.read_text(encoding='utf-8'))
        )
