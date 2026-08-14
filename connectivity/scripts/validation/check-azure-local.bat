@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0\..\.."

set "CLEAN=0"
set "RUN_KEY_VAULT=0"
set "RUN_STORAGE=0"
set "RUN_COSMOS=0"
set "RUN_REDIS=0"
set "HAS_MODULES=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--clean" (
    set "CLEAN=1"
    shift
    goto parse_args
)
if /I "%~1"=="key-vault" (
    set "RUN_KEY_VAULT=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="storage" (
    set "RUN_STORAGE=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="cosmos" (
    set "RUN_COSMOS=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)
if /I "%~1"=="redis" (
    set "RUN_REDIS=1"
    set "HAS_MODULES=1"
    shift
    goto parse_args
)

echo Unknown Azure-local validation module: %~1 1>&2
goto usage

:args_done
if "%HAS_MODULES%"=="0" (
    set "RUN_KEY_VAULT=1"
    set "RUN_STORAGE=1"
    set "RUN_COSMOS=1"
    set "RUN_REDIS=1"
)

where docker >nul 2>&1
if errorlevel 1 (
    echo docker is required for Azure-local integration tests. 1>&2
    exit /b 1
)

set "TARGET="
if "%RUN_KEY_VAULT%"=="1" set "TARGET=key-vault"
if "%RUN_STORAGE%"=="1" (
    if defined TARGET (
        set "TARGET=!TARGET!,storage"
    ) else (
        set "TARGET=storage"
    )
)
if "%RUN_COSMOS%"=="1" (
    if defined TARGET (
        set "TARGET=!TARGET!,cosmos"
    ) else (
        set "TARGET=cosmos"
    )
)
if "%RUN_REDIS%"=="1" (
    if defined TARGET (
        set "TARGET=!TARGET!,redis"
    ) else (
        set "TARGET=redis"
    )
)
if "%RUN_KEY_VAULT%"=="1" if "%RUN_STORAGE%"=="1" if "%RUN_COSMOS%"=="1" if "%RUN_REDIS%"=="1" set "TARGET=all"

set "COMPOSE_FILE=docker\azure-local\compose.yaml"
set "RUNNER_IMAGE=atlanticus-connectivity-azure-local-integration:local"

docker compose -f "%COMPOSE_FILE%" down -v --remove-orphans >nul 2>&1
if "%CLEAN%"=="1" docker image rm "%RUNNER_IMAGE%" >nul 2>&1

set "ATLANTICUS_AZURE_LOCAL_TARGET=%TARGET%"
docker compose -f "%COMPOSE_FILE%" up --build --abort-on-container-exit --exit-code-from connectivity-integration
set "DOCKER_CODE=!errorlevel!"
if not "!DOCKER_CODE!"=="0" docker compose -f "%COMPOSE_FILE%" logs floci-az connectivity-integration
docker compose -f "%COMPOSE_FILE%" down -v --remove-orphans >nul 2>&1
if "%CLEAN%"=="1" docker image rm "%RUNNER_IMAGE%" >nul 2>&1
if not "!DOCKER_CODE!"=="0" exit /b !DOCKER_CODE!

echo Azure-local connectivity validation passed: %TARGET%.
exit /b 0

:usage
echo Usage: %~nx0 [key-vault] [storage] [cosmos] [redis] [--clean] 1>&2
exit /b 2
