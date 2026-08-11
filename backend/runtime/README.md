# Atlanticus Job Runtime

`atlanticus-job-runtime==0.3.0` coordina jobs backend dentro de contenedores sin conocer
conectores, datasets, State ni reglas de ADA.

## Responsabilidades

- valida `ENVIRONMENT`, `APPLICATION` y `VOLUMEN_PATH`;
- ejecuta iteraciones dentro de un presupuesto temporal cooperativo;
- impide dos escritores para un mismo `job_key` mediante una lease renovable;
- detiene y falla la ejecución si pierde la lease o su heartbeat;
- convierte `SIGTERM` y `RuntimeCancellationRequested` en cierres controlados;
- compone la persistencia local y la extensión Azure de observabilidad;
- muestrea recursos sin permitir que una falla de telemetría detenga el negocio.

Runtime no crea clientes, no resuelve secretos, no administra pools de negocio y no fuerza la
terminación de librerías bloqueadas.

## Configuración

Las tres variables son obligatorias y no admiten normalizaciones ambiguas:

```text
ENVIRONMENT=local
APPLICATION=ada
VOLUMEN_PATH=/app/volumen
```

`ENVIRONMENT` admite `local`, `dev`, `uat`, `stg` o `prd`. La configuración pública
solo conserva ambiente, aplicación y volumen.

## Uso

```python
from atlanticus.runtime import JobDefinition, JobRuntimeContext, execute_job

JOB = JobDefinition(
    module_name='ada.processes.dispatch',
    service_name='dispatch',
    job_key='dispatch-materialization',
    sleep_seconds=0,
    iteration_timeout_seconds=300,
    execution_timeout_seconds=600,
    shutdown_grace_seconds=15,
    lease_timeout_seconds=120,
    lease_renew_seconds=30,
)


def run_iteration(context: JobRuntimeContext) -> None:
    context.raise_if_cancelled()


if __name__ == '__main__':
    execute_job(definition=JOB, iteration=run_iteration)
```

Los argumentos permitidos son `--environment`, `--debug` y `--run-once`. `--debug`
fuerza una sola iteración; los límites temporales permanecen en `JobDefinition`.

## Rutas

```text
${VOLUMEN_PATH}/${APPLICATION}/logs/${SERVICE}/day=YYYY-MM-DD/
${VOLUMEN_PATH}/${APPLICATION}/.runtime/leases/${JOB_KEY}.json
```

La lease se elimina durante un cierre normal. Si expira, el siguiente proceso registra
`execution.timed_out` para la ejecución anterior antes de continuar.

## Cancelación y lease

`JobRuntimeContext.should_stop`, `raise_if_cancelled()` y `wait()` forman el contrato
cooperativo. `SIGTERM` despierta `wait()` y conserva la primera razón de detención.

La pérdida de propiedad o un fallo del heartbeat solicita la detención del contexto y termina como
`execution.failed`. El runtime no mata hilos ni subprocesos de negocio; una operación externa debe
tener su propio timeout y revisar la cancelación entre pasos.

## Observabilidad

La persistencia local siempre mantiene el contrato de `atlanticus-observability`. Los perfiles
solo afectan Azure:

- `slim`: eventos operacionales, métricas y errores sanitizados en JSON;
- `diagnostic`: lo anterior más spans fallidos o lentos y contexto técnico acotado.

Un fallo de bootstrap Azure o del monitor de recursos genera un warning seguro y el job continúa
con la traza local.

## Validación

Desde `backend/`:

```bash
uv sync --locked --all-packages --group dev --no-editable
uv run --no-editable --all-packages ruff check runtime/src runtime/tests
uv run --no-editable --all-packages ruff format --check runtime/src runtime/tests
uv run --no-editable --all-packages pytest runtime/tests
uv run --no-editable --all-packages python -m compileall -q runtime/commented
uv build --package atlanticus-job-runtime --out-dir dist --clear
```
