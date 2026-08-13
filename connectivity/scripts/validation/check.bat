@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0\..\.."

set "CLEAN=0"
set "DOCKER=0"
set "ALL=0"
set "HAS_MODULES=0"
set "SEL_HTTP=0"
set "SEL_KEY_VAULT=0"
set "SEL_COSMOS=0"
set "SEL_SERVICE_BUS=0"
set "SEL_SQL=0"
set "SEL_STORAGE=0"
set "SEL_REDIS=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--clean" (
    set "CLEAN=1"
    shift
    goto parse_args
)
if /I "%~1"=="--docker" (
    set "DOCKER=1"
    shift
    goto parse_args
)
if /I "%~1"=="http-client" (
    set "SEL_HTTP=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="key-vault" (
    set "SEL_KEY_VAULT=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="cosmos" (
    set "SEL_COSMOS=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="service-bus" (
    set "SEL_SERVICE_BUS=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="sql" (
    set "SEL_SQL=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="storage" (
    set "SEL_STORAGE=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="redis" (
    set "SEL_REDIS=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)

echo Unknown validation module: %~1 1>&2
goto usage

:args_done
if "%HAS_MODULES%"=="0" (
    set "ALL=1"
    set "SEL_HTTP=1"
    set "SEL_KEY_VAULT=1"
    set "SEL_COSMOS=1"
    set "SEL_SERVICE_BUS=1"
    set "SEL_SQL=1"
    set "SEL_STORAGE=1"
    set "SEL_REDIS=1"
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
    for %%P in (http-client key-vault cosmos service-bus sql storage redis) do (
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
    if "!SEL_HTTP!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-http"
    if "!SEL_KEY_VAULT!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-key-vault"
    if "!SEL_COSMOS!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-cosmos"
    if "!SEL_SERVICE_BUS!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-service-bus"
    if "!SEL_SQL!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-sql"
    if "!SEL_STORAGE!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-storage"
    if "!SEL_REDIS!"=="1" set "SYNC_PACKAGES=!SYNC_PACKAGES! --package atlanticus-redis"

    call :run uv sync --python "%PYTHON_BIN%" --no-python-downloads %CACHE_ARG% --only-group dev --frozen
    if errorlevel 1 exit /b 1

    call :run uv sync --python "%PYTHON_BIN%" --no-python-downloads %CACHE_ARG% !SYNC_PACKAGES! --no-default-groups --inexact --frozen --no-editable
    if errorlevel 1 exit /b 1
)

if "!SEL_HTTP!"=="1" call :normalize_and_check_ruff http-client
if errorlevel 1 exit /b 1
if "!SEL_KEY_VAULT!"=="1" call :normalize_and_check_ruff key-vault
if errorlevel 1 exit /b 1
if "!SEL_COSMOS!"=="1" call :normalize_and_check_ruff cosmos
if errorlevel 1 exit /b 1
if "!SEL_SERVICE_BUS!"=="1" call :normalize_and_check_ruff service-bus
if errorlevel 1 exit /b 1
if "!SEL_SQL!"=="1" call :normalize_and_check_ruff sql
if errorlevel 1 exit /b 1
if "!SEL_STORAGE!"=="1" call :normalize_and_check_ruff storage
if errorlevel 1 exit /b 1
if "!SEL_REDIS!"=="1" call :normalize_and_check_ruff redis
if errorlevel 1 exit /b 1

if "!SEL_HTTP!"=="1" call :check_module http-client atlanticus.connectivity.http
if errorlevel 1 exit /b 1
if "!SEL_KEY_VAULT!"=="1" call :check_module key-vault atlanticus.connectivity.key_vault
if errorlevel 1 exit /b 1
if "!SEL_COSMOS!"=="1" call :check_module cosmos atlanticus.connectivity.cosmos
if errorlevel 1 exit /b 1
if "!SEL_SERVICE_BUS!"=="1" call :check_module service-bus atlanticus.connectivity.service_bus
if errorlevel 1 exit /b 1
if "!SEL_SQL!"=="1" call :check_module sql atlanticus.connectivity.sql
if errorlevel 1 exit /b 1
if "!SEL_STORAGE!"=="1" call :check_module storage atlanticus.connectivity.storage
if errorlevel 1 exit /b 1
if "!SEL_REDIS!"=="1" call :check_module redis atlanticus.connectivity.redis
if errorlevel 1 exit /b 1

if exist "dist" rmdir /s /q "dist"
mkdir "dist"

set /a SELECTED_COUNT=0
if "!SEL_HTTP!"=="1" call :build_module http-client
if errorlevel 1 exit /b 1
if "!SEL_HTTP!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_KEY_VAULT!"=="1" call :build_module key-vault
if errorlevel 1 exit /b 1
if "!SEL_KEY_VAULT!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_COSMOS!"=="1" call :build_module cosmos
if errorlevel 1 exit /b 1
if "!SEL_COSMOS!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_SERVICE_BUS!"=="1" call :build_module service-bus
if errorlevel 1 exit /b 1
if "!SEL_SERVICE_BUS!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_SQL!"=="1" call :build_module sql
if errorlevel 1 exit /b 1
if "!SEL_SQL!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_STORAGE!"=="1" call :build_module storage
if errorlevel 1 exit /b 1
if "!SEL_STORAGE!"=="1" set /a SELECTED_COUNT+=1
if "!SEL_REDIS!"=="1" call :build_module redis
if errorlevel 1 exit /b 1
if "!SEL_REDIS!"=="1" set /a SELECTED_COUNT+=1

set /a WHEEL_COUNT=0
if exist "dist\*.whl" (
    for %%F in (dist\*.whl) do set /a WHEEL_COUNT+=1
)
if not "!WHEEL_COUNT!"=="!SELECTED_COUNT!" (
    echo Expected !SELECTED_COUNT! wheels in dist, found !WHEEL_COUNT!. 1>&2
    exit /b 1
)

if "%DOCKER%"=="1" (
    if "!SEL_HTTP!"=="1" (
        where docker >nul 2>&1
        if errorlevel 1 (
            echo docker is required for HTTP integration tests. 1>&2
            exit /b 1
        )
        docker compose -f docker\http\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-http-integration:local >nul 2>&1
        call :run docker compose -f docker\http\compose.yaml up --build --abort-on-container-exit --exit-code-from http-integration
        set "DOCKER_CODE=!errorlevel!"
        if not "!DOCKER_CODE!"=="0" docker compose -f docker\http\compose.yaml logs http-fake-api http-integration
        docker compose -f docker\http\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-http-integration:local >nul 2>&1
        if not "!DOCKER_CODE!"=="0" exit /b !DOCKER_CODE!
    )
    if "!SEL_KEY_VAULT!"=="1" echo No Docker integration is defined for key-vault; unit validation completed.
    if "!SEL_COSMOS!"=="1" (
        where docker >nul 2>&1
        if errorlevel 1 (
            echo docker is required for Cosmos integration tests. 1>&2
            exit /b 1
        )
        docker compose -f docker\cosmos\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-cosmos-integration:local >nul 2>&1
        call :run docker compose -f docker\cosmos\compose.yaml up --build --abort-on-container-exit --exit-code-from cosmos-integration
        set "COSMOS_DOCKER_CODE=!errorlevel!"
        if not "!COSMOS_DOCKER_CODE!"=="0" docker compose -f docker\cosmos\compose.yaml logs cosmos-emulator cosmos-integration
        docker compose -f docker\cosmos\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-cosmos-integration:local >nul 2>&1
        if not "!COSMOS_DOCKER_CODE!"=="0" exit /b !COSMOS_DOCKER_CODE!
    )
    if "!SEL_SERVICE_BUS!"=="1" (
        where docker >nul 2>&1
        if errorlevel 1 (
            echo docker is required for Service Bus integration tests. 1>&2
            exit /b 1
        )
        docker compose -f docker\service-bus\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-service-bus-integration:local >nul 2>&1
        call :run docker compose -f docker\service-bus\compose.yaml up --build --abort-on-container-exit --exit-code-from service-bus-integration
        set "SERVICE_BUS_DOCKER_CODE=!errorlevel!"
        if not "!SERVICE_BUS_DOCKER_CODE!"=="0" docker compose -f docker\service-bus\compose.yaml logs servicebus-mssql servicebus-emulator service-bus-integration
        docker compose -f docker\service-bus\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-service-bus-integration:local >nul 2>&1
        if not "!SERVICE_BUS_DOCKER_CODE!"=="0" exit /b !SERVICE_BUS_DOCKER_CODE!
    )
    if "!SEL_SQL!"=="1" (
        where docker >nul 2>&1
        if errorlevel 1 (
            echo docker is required for SQL integration tests. 1>&2
            exit /b 1
        )
        docker compose -f docker\sql\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-sql-integration:local >nul 2>&1
        call :run docker compose -f docker\sql\compose.yaml up --build --abort-on-container-exit --exit-code-from sql-integration
        set "SQL_DOCKER_CODE=!errorlevel!"
        if not "!SQL_DOCKER_CODE!"=="0" docker compose -f docker\sql\compose.yaml logs sql-server sql-integration
        docker compose -f docker\sql\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-sql-integration:local >nul 2>&1
        if not "!SQL_DOCKER_CODE!"=="0" exit /b !SQL_DOCKER_CODE!
    )
    if "!SEL_STORAGE!"=="1" (
        where docker >nul 2>&1
        if errorlevel 1 (
            echo docker is required for Storage integration tests. 1>&2
            exit /b 1
        )
        docker compose -f docker\storage\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-storage-integration:local >nul 2>&1
        call :run docker compose -f docker\storage\compose.yaml up --build --abort-on-container-exit --exit-code-from storage-integration
        set "STORAGE_DOCKER_CODE=!errorlevel!"
        if not "!STORAGE_DOCKER_CODE!"=="0" docker compose -f docker\storage\compose.yaml logs azurite storage-integration
        docker compose -f docker\storage\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-storage-integration:local >nul 2>&1
        if not "!STORAGE_DOCKER_CODE!"=="0" exit /b !STORAGE_DOCKER_CODE!
    )
    if "!SEL_REDIS!"=="1" (
        where docker >nul 2>&1
        if errorlevel 1 (
            echo docker is required for Redis integration tests. 1>&2
            exit /b 1
        )
        docker compose -f docker\redis\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-redis-integration:local >nul 2>&1
        call :run docker compose -f docker\redis\compose.yaml up --build --abort-on-container-exit --exit-code-from redis-integration
        set "REDIS_DOCKER_CODE=!errorlevel!"
        if not "!REDIS_DOCKER_CODE!"=="0" docker compose -f docker\redis\compose.yaml logs redis-server redis-integration
        docker compose -f docker\redis\compose.yaml down -v --remove-orphans >nul 2>&1
        docker image rm atlanticus-redis-integration:local >nul 2>&1
        if not "!REDIS_DOCKER_CODE!"=="0" exit /b !REDIS_DOCKER_CODE!
    )
)

if "%ALL%"=="1" (
    echo Connectivity validation passed: 7 packages, 7 wheels.
) else if "!SELECTED_COUNT!"=="1" (
    echo Connectivity validation passed: 1 selected package, 1 wheel.
) else (
    echo Connectivity validation passed: !SELECTED_COUNT! selected packages, !WHEEL_COUNT! wheels.
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
call :run uv run --python "%PYTHON_BIN%" --no-python-downloads --no-sync pytest "%~1\tests\unit"
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
echo Usage: %~nx0 [module ...] [--clean] [--docker] 1>&2
echo Modules: http-client key-vault cosmos service-bus sql storage redis 1>&2
echo No modules validates the complete migrated connectivity workspace. 1>&2
exit /b 2
