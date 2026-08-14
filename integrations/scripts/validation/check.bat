@echo off
setlocal EnableExtensions
cd /d "%~dp0\..\.."

set "PYTHON_VERSION=3.14.2"
set "CLEAN=0"

if /I "%~1"=="--clean" (
    set "CLEAN=1"
    shift
)
if not "%~1"=="" (
    echo Usage: scripts\validation\check.bat [--clean] 1>&2
    exit /b 2
)

if "%CLEAN%"=="1" (
    if exist .venv rmdir /s /q .venv
    if exist dist rmdir /s /q dist
    if exist pi\contracts\build rmdir /s /q pi\contracts\build
    if exist pi\contracts\src\atlanticus_pi_contracts.egg-info rmdir /s /q pi\contracts\src\atlanticus_pi_contracts.egg-info
    if exist pi\web-api\build rmdir /s /q pi\web-api\build
    if exist pi\web-api\src\atlanticus_pi_web_api.egg-info rmdir /s /q pi\web-api\src\atlanticus_pi_web_api.egg-info
)

if not exist uv.lock (
    echo Missing integrations\uv.lock. Bootstrap it once with: uv lock --python 3.14.2 --no-python-downloads 1>&2
    exit /b 2
)

call :run uv lock --python %PYTHON_VERSION% --no-python-downloads --check || exit /b %errorlevel%
call :run uv sync --python %PYTHON_VERSION% --no-python-downloads --only-group dev --frozen || exit /b %errorlevel%
call :run uv sync --python %PYTHON_VERSION% --no-python-downloads --package atlanticus-pi-contracts --no-default-groups --inexact --frozen --no-editable || exit /b %errorlevel%
call :run uv sync --python %PYTHON_VERSION% --no-python-downloads --package atlanticus-pi-web-api --no-default-groups --inexact --frozen --no-editable || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff check --fix pi\contracts pi\web-api || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff format pi\contracts pi\web-api || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff check --fix pi\contracts\commented pi\web-api\commented || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff format pi\contracts\commented pi\web-api\commented || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff check pi\contracts pi\web-api || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync ruff format --check pi\contracts pi\web-api || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync pytest pi\contracts\tests\unit pi\web-api\tests\unit pi\web-api\tests\integration\local || exit /b %errorlevel%
call :run uv run --python %PYTHON_VERSION% --no-python-downloads --no-sync python -c "import atlanticus.integrations.pi.contracts; import atlanticus.integrations.pi.web_api" || exit /b %errorlevel%
if not exist dist mkdir dist
call :run uv build pi\contracts --python %PYTHON_VERSION% --no-python-downloads --wheel --out-dir dist || exit /b %errorlevel%
call :run uv build pi\web-api --python %PYTHON_VERSION% --no-python-downloads --wheel --out-dir dist || exit /b %errorlevel%

echo Integrations validation passed: pi-contracts + pi-web-api, 2 wheels.
exit /b 0

:run
echo ^> %*
%*
exit /b %errorlevel%
