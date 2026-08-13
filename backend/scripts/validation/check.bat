@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0\..\.."

set "CLEAN=0"
if "%~1"=="" goto args_done
if /I "%~1"=="--clean" (
    set "CLEAN=1"
    shift
) else (
    goto usage
)
if not "%~1"=="" goto usage
:args_done

where uv >nul 2>&1
if errorlevel 1 (
    echo uv is required. 1>&2
    exit /b 1
)

set "CACHE_ARG="
if "%CLEAN%"=="1" (
    if not defined UV_HTTP_TIMEOUT set "UV_HTTP_TIMEOUT=120"
    set "CACHE_ARG=--no-cache"
    if exist ".venv" rmdir /s /q ".venv"
    if exist "dist" rmdir /s /q "dist"
    for %%P in (kernel configuration datasets datasets-parquet datasets-runtime observability observability-azure state runtime) do (
        if exist "%%P\build" rmdir /s /q "%%P\build"
        if exist "%%P\.pytest_cache" rmdir /s /q "%%P\.pytest_cache"
        if exist "%%P\.ruff_cache" rmdir /s /q "%%P\.ruff_cache"
        for /d %%D in ("%%P\*.egg-info") do if exist "%%~fD" rmdir /s /q "%%~fD"
    )
)

set "PYTHON_BIN="
for /f "usebackq delims=" %%P in (`uv python find 3.14.2 --no-python-downloads`) do set "PYTHON_BIN=%%P"
if not defined PYTHON_BIN exit /b 1

"%PYTHON_BIN%" -c "import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 14, 2) else 1)"
if errorlevel 1 exit /b 1

call :run uv lock --python "%PYTHON_BIN%" --no-python-downloads %CACHE_ARG% --check
if errorlevel 1 exit /b 1

call :run uv sync --python "%PYTHON_BIN%" --no-python-downloads %CACHE_ARG% --all-packages --group dev --frozen --no-editable
if errorlevel 1 exit /b 1

call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync ruff check .
if errorlevel 1 exit /b 1

call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync ruff format --check .
if errorlevel 1 exit /b 1

for %%P in (kernel configuration datasets datasets-parquet datasets-runtime observability observability-azure state runtime) do (
    call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync pytest "%%P\tests"
    if errorlevel 1 exit /b 1
)

call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync python -c "import atlanticus.configuration, atlanticus.datasets, atlanticus.datasets.parquet, atlanticus.datasets.runtime, atlanticus.kernel, atlanticus.observability, atlanticus.observability_azure, atlanticus.runtime, atlanticus.state"
if errorlevel 1 exit /b 1

if exist "dist" rmdir /s /q "dist"
mkdir "dist"

for %%P in (kernel configuration datasets datasets-parquet datasets-runtime observability observability-azure state runtime) do (
    if exist "%%P\build" rmdir /s /q "%%P\build"
    for /d %%D in ("%%P\*.egg-info") do if exist "%%~fD" rmdir /s /q "%%~fD"

    call :run uv build "%%P" --python "%PYTHON_BIN%" --no-python-downloads %CACHE_ARG% --wheel --out-dir dist
    if errorlevel 1 exit /b 1
)

set /a WHEEL_COUNT=0
if exist "dist\*.whl" (
    for %%F in (dist\*.whl) do set /a WHEEL_COUNT+=1
)
if not "!WHEEL_COUNT!"=="9" (
    echo Expected 9 wheels in dist, found !WHEEL_COUNT!. 1>&2
    exit /b 1
)

echo Backend validation passed: 9 packages, 9 wheels.
exit /b 0

:run
echo ^> %*
%*
exit /b %errorlevel%

:usage
echo Usage: %~nx0 [--clean] 1>&2
exit /b 2
