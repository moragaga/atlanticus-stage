import ast
from pathlib import Path


def test_commented_mirror_preserves_productive_behavior() -> None:
    root = Path(__file__).resolve().parents[1]
    productive = root / 'src' / 'ada' / 'processes' / 'pi_web_api'
    commented = root / 'commented' / 'ada' / 'processes' / 'pi_web_api'

    productive_files = {path.relative_to(productive) for path in productive.rglob('*.py')}
    commented_files = {path.relative_to(commented) for path in commented.rglob('*.py')}
    assert commented_files == productive_files

    for relative_path in sorted(productive_files):
        productive_ast = ast.dump(
            ast.parse((productive / relative_path).read_text(encoding='utf-8')),
            include_attributes=False,
        )
        commented_ast = ast.dump(
            ast.parse((commented / relative_path).read_text(encoding='utf-8')),
            include_attributes=False,
        )
        assert commented_ast == productive_ast, str(relative_path)
