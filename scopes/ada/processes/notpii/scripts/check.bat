@echo off
setlocal enabledelayedexpansion
set "PYTHON_VERSION=3.14.2"
set "RUFF_VERSION=0.15.22"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROCESS_ROOT=%%~fI"
for %%I in ("%PROCESS_ROOT%\..\..\..\..") do set "REPOSITORY_ROOT=%%~fI"
set "ARTIFACT_ROOT=%REPOSITORY_ROOT%\artifacts\processes"
set "ARTIFACT_PATH=%ARTIFACT_ROOT%\notpii"
set "BUNDLER=%REPOSITORY_ROOT%\scopes\ada\scripts\processes\process_bundle.py"

if "%~1"=="--clean" (
    if exist "%ARTIFACT_PATH%" rmdir /s /q "%ARTIFACT_PATH%"
    if exist "%PROCESS_ROOT%\.venv" rmdir /s /q "%PROCESS_ROOT%\.venv"
)

if not exist "%BUNDLER%" (
    echo Process bundle script not found: %BUNDLER% 1>&2
    exit /b 1
)

echo [1/6] Applying safe Ruff fixes to process source
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project --with "ruff==%RUFF_VERSION%" ruff check --fix --exit-zero --config "%PROCESS_ROOT%\pyproject.toml" "%PROCESS_ROOT%\src" "%PROCESS_ROOT%\tests" || exit /b 1

echo [2/6] Formatting process source
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project --with "ruff==%RUFF_VERSION%" ruff format --config "%PROCESS_ROOT%\pyproject.toml" "%PROCESS_ROOT%\src" "%PROCESS_ROOT%\tests" || exit /b 1

echo [3/6] Building and validating transport artifact
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-project "%BUNDLER%" "%PROCESS_ROOT%" --repository-root "%REPOSITORY_ROOT%" --output-root "%ARTIFACT_ROOT%" || exit /b 1

cd /d "%ARTIFACT_PATH%" || exit /b 1

echo [4/6] Verifying transport bundle contents
for %%D in (tests commented docs scripts) do (
    if exist "%%D" (
        echo Transport bundle must not contain %%D: %ARTIFACT_PATH% 1>&2
        exit /b 1
    )
)
for %%F in (FIRST_STEP.txt .env.detail config.detail.json secrets.detail.json pyproject.toml uv.lock wheels src) do (
    if not exist "%%F" (
        echo Transport bundle is missing %%F: %ARTIFACT_PATH% 1>&2
        exit /b 1
    )
)

echo [5/6] Installing locked transport runtime
uv sync --python "%PYTHON_VERSION%" --no-python-downloads --no-cache --frozen || exit /b 1

echo [6/6] Verifying transport runtime
uv run --python "%PYTHON_VERSION%" --no-python-downloads --no-sync python -c "import sys; import ada.processes.notpii; assert sys.version_info[:3] == (3, 14, 2), sys.version" || exit /b 1

if exist .venv rmdir /s /q .venv
for /d /r src %%D in (*.egg-info) do if exist "%%~fD" rmdir /s /q "%%~fD"
for /d /r src %%D in (*.egg-info) do (
    if exist "%%~fD" (
        echo Transport bundle must not contain generated egg-info metadata: %ARTIFACT_PATH% 1>&2
        exit /b 1
    )
)

echo NOTPII transport artifact validated: %ARTIFACT_PATH%
exit /b 0
