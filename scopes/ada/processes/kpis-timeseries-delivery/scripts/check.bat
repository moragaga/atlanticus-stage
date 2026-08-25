@echo off
setlocal enabledelayedexpansion

set "PYTHON_VERSION=3.14.2"
set "RUFF_VERSION=0.15.22"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROCESS_ROOT=%%~fI"
for %%I in ("%PROCESS_ROOT%\..\..\..\..") do set "REPOSITORY_ROOT=%%~fI"
set "ARTIFACT_ROOT=%REPOSITORY_ROOT%\artifacts\processes"
set "ARTIFACT_PATH=%ARTIFACT_ROOT%\kpis-timeseries-delivery"
set "BUNDLER=%REPOSITORY_ROOT%\scopes\ada\scripts\processes\process_bundle.py"

if "%~1"=="--clean" (
    if exist "%ARTIFACT_PATH%" rmdir /s /q "%ARTIFACT_PATH%"
    if exist "%PROCESS_ROOT%\.venv" rmdir /s /q "%PROCESS_ROOT%\.venv"
)

if not exist "%BUNDLER%" (
    echo Process bundle script not found: %BUNDLER% 1>&2
    exit /b 1
)

uv run --python %PYTHON_VERSION% --no-python-downloads --no-project --with ruff==%RUFF_VERSION% ruff check --fix --exit-zero --config "%PROCESS_ROOT%\pyproject.toml" "%PROCESS_ROOT%\src" "%PROCESS_ROOT%\tests" "%PROCESS_ROOT%\commented" || exit /b 1
uv run --python %PYTHON_VERSION% --no-python-downloads --no-project --with ruff==%RUFF_VERSION% ruff format --config "%PROCESS_ROOT%\pyproject.toml" "%PROCESS_ROOT%\src" "%PROCESS_ROOT%\tests" "%PROCESS_ROOT%\commented" || exit /b 1
uv run --python %PYTHON_VERSION% --no-python-downloads --no-project "%BUNDLER%" "%PROCESS_ROOT%" --repository-root "%REPOSITORY_ROOT%" --output-root "%ARTIFACT_ROOT%" || exit /b 1

pushd "%ARTIFACT_PATH%" || exit /b 1
for %%D in (tests commented docs scripts) do if exist "%%D" (
    echo Transport bundle must not contain %%D: %ARTIFACT_PATH% 1>&2
    popd
    exit /b 1
)
for %%F in (FIRST_STEP.txt .env.detail config.detail.json secrets.detail.json pyproject.toml uv.lock wheels src) do if not exist "%%F" (
    echo Transport bundle is missing %%F: %ARTIFACT_PATH% 1>&2
    popd
    exit /b 1
)
uv sync --python %PYTHON_VERSION% --no-python-downloads --no-cache --frozen || exit /b 1
uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync python -c "import sys; import ada.processes.kpis_timeseries_delivery; assert sys.version_info[:3] == (3, 14, 2), sys.version" || exit /b 1
if exist ".venv" rmdir /s /q ".venv"
popd

echo KPI Timeseries Delivery transport artifact validated: %ARTIFACT_PATH%
