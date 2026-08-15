@echo off
setlocal enabledelayedexpansion
set "PYTHON_VERSION=3.14.2"
set "PYTEST_VERSION=9.1.1"
set "RUFF_VERSION=0.15.22"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%\..\..") do set "ADA_ROOT=%%~fI"
set "RUFF_CONFIG=%SCRIPT_DIR%pyproject.toml"

if "%~1"=="--clean" (
    if exist "%SCRIPT_DIR%.pytest_cache" rmdir /s /q "%SCRIPT_DIR%.pytest_cache"
    if exist "%SCRIPT_DIR%.ruff_cache" rmdir /s /q "%SCRIPT_DIR%.ruff_cache"
    if exist "%SCRIPT_DIR%tests\__pycache__" rmdir /s /q "%SCRIPT_DIR%tests\__pycache__"
    if exist "%SCRIPT_DIR%commented\__pycache__" rmdir /s /q "%SCRIPT_DIR%commented\__pycache__"
    if exist "%SCRIPT_DIR%__pycache__" rmdir /s /q "%SCRIPT_DIR%__pycache__"
)

echo [1/6] Applying safe Ruff fixes to process tooling source
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project --with "ruff==%RUFF_VERSION%" ruff check --fix --exit-zero --config "%RUFF_CONFIG%" "%SCRIPT_DIR%process_bundle.py" "%SCRIPT_DIR%tests" || exit /b 1

echo [2/6] Formatting process tooling source
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project --with "ruff==%RUFF_VERSION%" ruff format --config "%RUFF_CONFIG%" "%SCRIPT_DIR%process_bundle.py" "%SCRIPT_DIR%tests" || exit /b 1

set "VALIDATION_FAILED=0"

echo [check] Ruff lint
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project --with "ruff==%RUFF_VERSION%" ruff check --config "%RUFF_CONFIG%" "%SCRIPT_DIR%process_bundle.py" "%SCRIPT_DIR%tests"
if errorlevel 1 (
    echo [fail] Ruff lint 1>&2
    set "VALIDATION_FAILED=1"
) else (
    echo [pass] Ruff lint
)

echo [check] Ruff format verification
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project --with "ruff==%RUFF_VERSION%" ruff format --check --config "%RUFF_CONFIG%" "%SCRIPT_DIR%process_bundle.py" "%SCRIPT_DIR%tests"
if errorlevel 1 (
    echo [fail] Ruff format verification 1>&2
    set "VALIDATION_FAILED=1"
) else (
    echo [pass] Ruff format verification
)

echo [check] Pytest
cd /d "%ADA_ROOT%" || exit /b 1
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project --with "pytest==%PYTEST_VERSION%" python -m pytest -ra scripts/processes/tests
if errorlevel 1 (
    echo [fail] Pytest 1>&2
    set "VALIDATION_FAILED=1"
) else (
    echo [pass] Pytest
)

echo [check] Commented mirror compilation
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project python -m compileall -q "%SCRIPT_DIR%commented"
if errorlevel 1 (
    echo [fail] Commented mirror compilation 1>&2
    set "VALIDATION_FAILED=1"
) else (
    echo [pass] Commented mirror compilation
)

if not "%VALIDATION_FAILED%"=="0" (
    echo ADA process tooling validation failed: %SCRIPT_DIR% 1>&2
    exit /b 1
)

echo ADA process tooling validated: %SCRIPT_DIR%
exit /b 0
