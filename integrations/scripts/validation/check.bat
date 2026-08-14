@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."

set "PYTHON_VERSION=3.14.2"
set "CLEAN=0"

if /I "%~1"=="pi-contracts" shift
if /I "%~1"=="--clean" (
    set "CLEAN=1"
    shift
)
if not "%~1"=="" (
    echo Usage: scripts\validation\check.bat [pi-contracts] [--clean] 1>&2
    exit /b 2
)

if "%CLEAN%"=="1" (
    if exist .venv rmdir /s /q .venv
    if exist dist rmdir /s /q dist
    if exist pi\contracts\build rmdir /s /q pi\contracts\build
)

if not exist uv.lock (
    echo Missing integrations\uv.lock. Bootstrap it once with: uv lock --python 3.14.2 --no-python-downloads 1>&2
    exit /b 2
)

call :run uv lock --python %PYTHON_VERSION% --no-python-downloads --check || exit /b %errorlevel%
call :run uv sync --python %PYTHON_VERSION% --no-python-downloads --only-group dev --frozen || exit /b %errorlevel%
call :run uv sync --python %PYTHON_VERSION% --no-python-downloads --package atlanticus-pi-contracts --no-default-groups --inexact --frozen --no-editable || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff check --fix pi\contracts || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff format pi\contracts || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff check --fix pi\contracts\commented || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff format pi\contracts\commented || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff check pi\contracts || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff format --check pi\contracts || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync pytest pi\contracts\tests\unit || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync python -c "import atlanticus.integrations.pi.contracts" || exit /b %errorlevel%
if not exist dist mkdir dist
call :run uv build pi\contracts --python %PYTHON_VERSION% --no-python-downloads --wheel --out-dir dist || exit /b %errorlevel%

echo Integrations validation passed: pi-contracts, 1 wheel.
exit /b 0

:run
echo ^> %*
%*
exit /b %errorlevel%
