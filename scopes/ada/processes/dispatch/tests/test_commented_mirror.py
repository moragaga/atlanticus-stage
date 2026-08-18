import ast
from pathlib import Path


def test_commented_tree_is_behaviorally_equivalent() -> None:
    process_root = Path(__file__).resolve().parents[1]
    source_root = process_root / 'src' / 'ada' / 'processes' / 'dispatch'
    commented_root = process_root / 'commented' / 'ada' / 'processes' / 'dispatch'
    source_files = sorted(path.relative_to(source_root) for path in source_root.rglob('*.py'))
    commented_files = sorted(
        path.relative_to(commented_root) for path in commented_root.rglob('*.py')
    )

    assert commented_files == source_files
    for relative in source_files:
        source_ast = ast.dump(
            ast.parse((source_root / relative).read_text()), include_attributes=False
        )
        commented_ast = ast.dump(
            ast.parse((commented_root / relative).read_text()), include_attributes=False
        )
        assert commented_ast == source_ast, relative
