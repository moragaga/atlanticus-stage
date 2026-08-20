@echo off
setlocal
cd /d "%~dp0\.."

uv lock --python 3.14.2 || exit /b 1
uv sync --python 3.14.2 --frozen --group dev || exit /b 1
uv run --python 3.14.2 --frozen ruff check src tests || exit /b 1
uv run --python 3.14.2 --frozen ruff format --check src tests || exit /b 1
uv run --python 3.14.2 --frozen pytest tests || exit /b 1
