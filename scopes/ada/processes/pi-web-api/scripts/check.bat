@echo off
setlocal enabledelayedexpansion
set "PYTHON_VERSION=3.14.2"
set "RUFF_VERSION=0.15.22"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROCESS_ROOT=%%~fI"
for %%I in ("%PROCESS_ROOT%\..\..\..\..") do set "REPOSITORY_ROOT=%%~fI"
set "ARTIFACT_ROOT=%REPOSITORY_ROOT%\artifacts\processes"
set "ARTIFACT_PATH=%ARTIFACT_ROOT%\pi-web-api"
set "BUNDLER=%REPOSITORY_ROOT%\scopes\ada\scripts\processes\process_bundle.py"

if "%~1"=="--clean" (
    if exist "%ARTIFACT_PATH%" rmdir /s /q "%ARTIFACT_PATH%"
    if exist "%PROCESS_ROOT%\.venv" rmdir /s /q "%PROCESS_ROOT%\.venv"
)

if not exist "%BUNDLER%" (
    echo Process bundle script not found: %BUNDLER% 1>&2
    exit /b 1
)

echo [1/8] Applying safe Ruff fixes to process source
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project --with "ruff==%RUFF_VERSION%" ruff check --fix --exit-zero --config "%PROCESS_ROOT%\pyproject.toml" "%PROCESS_ROOT%\src" "%PROCESS_ROOT%\tests" || exit /b 1

echo [2/8] Formatting process source
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project --with "ruff==%RUFF_VERSION%" ruff format --config "%PROCESS_ROOT%\pyproject.toml" "%PROCESS_ROOT%\src" "%PROCESS_ROOT%\tests" || exit /b 1

echo [3/8] Building process artifact
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project "%BUNDLER%" "%PROCESS_ROOT%" --repository-root "%REPOSITORY_ROOT%" --output-root "%ARTIFACT_ROOT%" || exit /b 1

cd /d "%ARTIFACT_PATH%" || exit /b 1

echo [4/8] Installing locked artifact environment
uv sync --python "%PYTHON_VERSION%" --no-python-downloads --no-cache --group dev --frozen || exit /b 1

echo [5/8] Verifying Python runtime
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-sync python -c "import sys; assert sys.version_info[:3] == (3, 14, 2), sys.version" || exit /b 1

set "VALIDATION_FAILED=0"

echo [check] Ruff lint
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-sync ruff check .
if errorlevel 1 (
    echo [fail] Ruff lint 1>&2
    set "VALIDATION_FAILED=1"
) else (
    echo [pass] Ruff lint
)

echo [check] Ruff format verification
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-sync ruff format --check .
if errorlevel 1 (
    echo [fail] Ruff format verification 1>&2
    set "VALIDATION_FAILED=1"
) else (
    echo [pass] Ruff format verification
)

echo [check] Pytest
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-sync python -m pytest -ra tests
if errorlevel 1 (
    echo [fail] Pytest 1>&2
    set "VALIDATION_FAILED=1"
) else (
    echo [pass] Pytest
)

if exist commented (
    echo [check] Commented mirror compilation
    uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-sync python -m compileall -q commented
    if errorlevel 1 (
        echo [fail] Commented mirror compilation 1>&2
        set "VALIDATION_FAILED=1"
    ) else (
        echo [pass] Commented mirror compilation
    )
)

if exist .venv rmdir /s /q .venv

if not "%VALIDATION_FAILED%"=="0" (
    echo PI Web API process artifact validation failed: %ARTIFACT_PATH% 1>&2
    exit /b 1
)

echo PI Web API process artifact validated: %ARTIFACT_PATH%
exit /b 0
