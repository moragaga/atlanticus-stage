@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0\..\.."

set "CLEAN=0"
set "ALL=0"
set "HAS_MODULES=0"
set "SEL_KERNEL=0"
set "SEL_CONFIGURATION=0"
set "SEL_DATASETS=0"
set "SEL_DATASETS_PARQUET=0"
set "SEL_DATASETS_RUNTIME=0"
set "SEL_OBSERVABILITY=0"
set "SEL_OBSERVABILITY_AZURE=0"
set "SEL_STATE=0"
set "SEL_RUNTIME=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--clean" (
    set "CLEAN=1"
    shift
    goto parse_args
)
if /I "%~1"=="kernel" (
    set "SEL_KERNEL=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="configuration" (
    set "SEL_CONFIGURATION=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="datasets" (
    set "SEL_DATASETS=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="datasets-parquet" (
    set "SEL_DATASETS_PARQUET=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="datasets-runtime" (
    set "SEL_DATASETS_RUNTIME=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="observability" (
    set "SEL_OBSERVABILITY=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="observability-azure" (
    set "SEL_OBSERVABILITY_AZURE=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="state" (
    set "SEL_STATE=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="runtime" (
    set "SEL_RUNTIME=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)

echo Unknown validation module: %~1 1>&2
goto usage

:args_done
if "%HAS_MODULES%"=="0" (
    set "ALL=1"
    set "SEL_KERNEL=1"
    set "SEL_CONFIGURATION=1"
    set "SEL_DATASETS=1"
    set "SEL_DATASETS_PARQUET=1"
    set "SEL_DATASETS_RUNTIME=1"
    set "SEL_OBSERVABILITY=1"
    set "SEL_OBSERVABILITY_AZURE=1"
    set "SEL_STATE=1"
    set "SEL_RUNTIME=1"
)

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

if "%ALL%"=="1" (
    call :run uv sync --python "%PYTHON_BIN%" --no-python-downloads %CACHE_ARG% --all-packages --group dev --frozen --no-editable
    if errorlevel 1 exit /b 1
) else (
    set "SYNC_PACKAGES="
    if "!SEL_KERNEL!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-kernel"
    if "!SEL_CONFIGURATION!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-configuration"
    if "!SEL_DATASETS!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-datasets"
    if "!SEL_DATASETS_PARQUET!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-datasets-parquet"
    if "!SEL_DATASETS_RUNTIME!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-datasets-runtime"
    if "!SEL_OBSERVABILITY!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-observability"
    if "!SEL_OBSERVABILITY_AZURE!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-observability-azure"
    if "!SEL_STATE!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-state"
    if "!SEL_RUNTIME!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-job-runtime"

    call :run uv sync --python "%PYTHON_BIN%" --no-python-downloads %CACHE_ARG% --only-group dev --frozen
    if errorlevel 1 exit /b 1

    call :run uv sync --python "%PYTHON_BIN%" --no-python-downloads %CACHE_ARG% !SYNC_PACKAGES! --no-default-groups --inexact --frozen --no-editable
    if errorlevel 1 exit /b 1
)

if "!SEL_KERNEL!"=="1" call :normalize_and_check_ruff kernel
if errorlevel 1 exit /b 1
if "!SEL_CONFIGURATION!"=="1" call :normalize_and_check_ruff configuration
if errorlevel 1 exit /b 1
if "!SEL_DATASETS!"=="1" call :normalize_and_check_ruff datasets
if errorlevel 1 exit /b 1
if "!SEL_DATASETS_PARQUET!"=="1" call :normalize_and_check_ruff datasets-parquet
if errorlevel 1 exit /b 1
if "!SEL_DATASETS_RUNTIME!"=="1" call :normalize_and_check_ruff datasets-runtime
if errorlevel 1 exit /b 1
if "!SEL_OBSERVABILITY!"=="1" call :normalize_and_check_ruff observability
if errorlevel 1 exit /b 1
if "!SEL_OBSERVABILITY_AZURE!"=="1" call :normalize_and_check_ruff observability-azure
if errorlevel 1 exit /b 1
if "!SEL_STATE!"=="1" call :normalize_and_check_ruff state
if errorlevel 1 exit /b 1
if "!SEL_RUNTIME!"=="1" call :normalize_and_check_ruff runtime
if errorlevel 1 exit /b 1

if "!SEL_KERNEL!"=="1" call :check_module kernel atlanticus.kernel
if errorlevel 1 exit /b 1
if "!SEL_CONFIGURATION!"=="1" call :check_module configuration atlanticus.configuration
if errorlevel 1 exit /b 1
if "!SEL_DATASETS!"=="1" call :check_module datasets atlanticus.datasets
if errorlevel 1 exit /b 1
if "!SEL_DATASETS_PARQUET!"=="1" call :check_module datasets-parquet atlanticus.datasets.parquet
if errorlevel 1 exit /b 1
if "!SEL_DATASETS_RUNTIME!"=="1" call :check_module datasets-runtime atlanticus.datasets.runtime
if errorlevel 1 exit /b 1
if "!SEL_OBSERVABILITY!"=="1" call :check_module observability atlanticus.observability
if errorlevel 1 exit /b 1
if "!SEL_OBSERVABILITY_AZURE!"=="1" call :check_module observability-azure atlanticus.observability_azure
if errorlevel 1 exit /b 1
if "!SEL_STATE!"=="1" call :check_module state atlanticus.state
if errorlevel 1 exit /b 1
if "!SEL_RUNTIME!"=="1" call :check_module runtime atlanticus.runtime
if errorlevel 1 exit /b 1

if exist "dist" rmdir /s /q "dist"
mkdir "dist"

set /a SELECTED_COUNT=0
if "!SEL_KERNEL!"=="1" call :build_module kernel
if errorlevel 1 exit /b 1
if "!SEL_KERNEL!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_CONFIGURATION!"=="1" call :build_module configuration
if errorlevel 1 exit /b 1
if "!SEL_CONFIGURATION!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_DATASETS!"=="1" call :build_module datasets
if errorlevel 1 exit /b 1
if "!SEL_DATASETS!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_DATASETS_PARQUET!"=="1" call :build_module datasets-parquet
if errorlevel 1 exit /b 1
if "!SEL_DATASETS_PARQUET!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_DATASETS_RUNTIME!"=="1" call :build_module datasets-runtime
if errorlevel 1 exit /b 1
if "!SEL_DATASETS_RUNTIME!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_OBSERVABILITY!"=="1" call :build_module observability
if errorlevel 1 exit /b 1
if "!SEL_OBSERVABILITY!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_OBSERVABILITY_AZURE!"=="1" call :build_module observability-azure
if errorlevel 1 exit /b 1
if "!SEL_OBSERVABILITY_AZURE!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_STATE!"=="1" call :build_module state
if errorlevel 1 exit /b 1
if "!SEL_STATE!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_RUNTIME!"=="1" call :build_module runtime
if errorlevel 1 exit /b 1
if "!SEL_RUNTIME!"=="1" set /a SELECTED_COUNT+=1

set /a WHEEL_COUNT=0
if exist "dist\*.whl" (
    for %%F in (dist\*.whl) do set /a WHEEL_COUNT+=1
)
if not "!WHEEL_COUNT!"=="!SELECTED_COUNT!" (
    echo Expected !SELECTED_COUNT! wheels in dist, found !WHEEL_COUNT!. 1>&2
    exit /b 1
)

if "%ALL%"=="1" (
    echo Backend validation passed: 9 packages, 9 wheels.
) else if "!SELECTED_COUNT!"=="1" (
    echo Backend validation passed: 1 selected package, 1 wheel.
) else (
    echo Backend validation passed: !SELECTED_COUNT! selected packages, !WHEEL_COUNT! wheels.
)
exit /b 0


:normalize_and_check_ruff
call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync ruff check --fix "%~1"
if errorlevel 1 exit /b 1
call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync ruff format "%~1"
if errorlevel 1 exit /b 1

if exist "%~1\commented" (
    for /r "%~1\commented" %%F in (*.py) do (
        call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync ruff check --fix "%%~fF"
        if errorlevel 1 exit /b 1
        call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync ruff format "%%~fF"
        if errorlevel 1 exit /b 1
    )
)

call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync ruff check "%~1"
if errorlevel 1 exit /b 1
call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync ruff format --check "%~1"
if errorlevel 1 exit /b 1
exit /b 0

:check_module
call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync pytest "%~1\tests"
if errorlevel 1 exit /b 1
call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync python -c "import %~2"
exit /b %errorlevel%

:build_module
if exist "%~1\build" rmdir /s /q "%~1\build"
for /d %%D in ("%~1\*.egg-info") do if exist "%%~fD" rmdir /s /q "%%~fD"
call :run uv build "%~1" --python "%PYTHON_BIN%" --no-python-downloads %CACHE_ARG% --wheel --out-dir dist
exit /b %errorlevel%

:run
echo ^> %*
%*
exit /b %errorlevel%

:usage
echo Usage: %~nx0 [module ...] [--clean] 1>&2
echo Modules: kernel configuration datasets datasets-parquet datasets-runtime observability observability-azure state runtime 1>&2
echo No modules validates the complete backend. 1>&2
exit /b 2
