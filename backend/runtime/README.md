# Atlanticus Job Runtime

`atlanticus-job-runtime==0.6.0` coordina ejecución, ventana temporal, lease, autoridad y cierre de jobs backend Atlanticus sin conocer conectores, datasets, State ni reglas de ADA.

## Contrato estable R20C

Runtime mantiene `RELATIVE` como modo por defecto y agrega `SCHEDULED_EXTERNAL` de forma opt-in. La presencia de cron efectivo no cambia la lógica de negocio del proceso: sólo delimita la autoridad temporal de la invocación.

Responsabilidades principales:

- valida `ENVIRONMENT`, `APPLICATION` y `VOLUMEN_PATH`;
- construye una ventana efectiva de ejecución a partir del presupuesto Runtime, el slot scheduled y el platform timeout conocido;
- impide dos escritores para un mismo `job_key` mediante una lease renovable;
- conserva una `generation` monotónica durable para el ownership;
- deduplica slots scheduled completados y permite reintentar slots incompletos;
- expone `lease_generation` y `assert_lease_current()` al consumidor para fencing lógico antes de commits protegidos;
- admite hooks opcionales `recovery` y `drain` bajo la misma autoridad de lease;
- mantiene heartbeat durante recovery, ejecución y drain;
- convierte `SIGTERM` y `RuntimeCancellationRequested` en cierres cooperativos;
- compone la persistencia local y la extensión Azure de observabilidad;
- muestrea recursos sin permitir que una falla de telemetría detenga el negocio.

Runtime no crea clientes, no resuelve secretos, no administra pools de negocio, no lee archivos de deployment y no implementa persistencia transaccional del consumidor.

`SCHEDULED_RESIDENT` no forma parte de la API disponible en `0.6.0`.

## Configuración

Las variables base son obligatorias:

```text
ENVIRONMENT=local
APPLICATION=ada
VOLUMEN_PATH=/app/volumen
```

`ENVIRONMENT` admite `local`, `dev`, `uat`, `stg` o `prd`.

La metadata scheduled es opcional y efectiva para una invocación concreta:

```text
ATLANTICUS_JOB_SCHEDULE_CRON=0 */2 * * *
ATLANTICUS_JOB_SCHEDULE_TIMEZONE=UTC
ATLANTICUS_JOB_PLATFORM_TIMEOUT_SECONDS=300
```

Reglas:

- sin `ATLANTICUS_JOB_SCHEDULE_CRON`, la ejecución permanece en `RELATIVE`;
- `ATLANTICUS_JOB_SCHEDULE_TIMEZONE` usa `UTC` por defecto y requiere cron cuando se declara explícitamente;
- cron, timezone o platform timeout inválidos fallan de forma explícita;
- Runtime no lee `config.detail.json`, `config.json`, `secrets.detail.json` ni `secrets.json`;
- el launcher o deployment entrega únicamente la metadata operacional efectiva mediante el environment.

## Execution Window

En `RELATIVE`, la frontera nace en `started_at + execution_timeout_seconds`.

En `SCHEDULED_EXTERNAL`, Runtime resuelve `scheduled_at` y `next_scheduled_at` desde el cron efectivo. La ejecución no recibe un presupuesto nuevo por arrancar tarde dentro de un slot.

La frontera final es el límite más restrictivo entre:

```text
runtime deadline
next scheduled slot, cuando existe
platform deadline, cuando existe
```

`shutdown_grace_seconds` se reserva dentro de esa misma frontera. Todos los límites son máximos: un job puede terminar naturalmente antes.

`run_once` sigue significando como máximo una iteración. No desactiva timeout, lease, scheduling ni autoridad.

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

Los hooks de lifecycle son opcionales:

```python
def recover(context: JobRuntimeContext) -> None:
    context.assert_lease_current()


def drain(context: JobRuntimeContext) -> None:
    context.assert_lease_current()


execute_job(
    definition=JOB,
    iteration=run_iteration,
    recovery=recover,
    drain=drain,
)
```

Los argumentos permitidos son `--environment`, `--debug` y `--run-once`. `--debug` fuerza una sola iteración; los límites temporales permanecen en `JobDefinition`.

## Lease y autoridad durable

Runtime separa ownership activo de autoridad durable:

```text
${VOLUMEN_PATH}/${APPLICATION}/.runtime/
├── leases/${JOB_KEY}.json
└── authority/${JOB_KEY}.json
```

La lease activa contiene ownership efímero. El registro de autoridad conserva la `generation` monotónica y el último slot scheduled completado.

Un cierre normal elimina la lease, pero no retrocede la generación durable. Un owner expirado no puede revivir la misma generación. Si un slot scheduled no completa recovery, iteration y drain correctamente, no se marca como completado y una generación posterior puede reintentarlo.

La renovación scheduled nunca puede extender la lease más allá de la autoridad temporal efectiva de la invocación.

## Recovery, run y drain

El lifecycle estable es:

```text
acquire lease
  -> bind generation + authority
  -> recovery(context), opcional
  -> iteration(context)
  -> drain(context), opcional
  -> release
```

Recovery, run y drain comparten la misma lease generation. El heartbeat permanece activo hasta entrar en release.

- si recovery falla, no comienza el negocio;
- si drain falla, un slot scheduled no queda consumido como completado;
- `SIGTERM` solicita stop cooperativo y permite avanzar a drain/release;
- los consumidores históricos pueden seguir llamando `execute_job(definition=..., iteration=...)` sin hooks nuevos.

## Fencing

`JobRuntimeContext` expone:

```text
context.lease_generation
context.assert_lease_current()
```

`assert_lease_current()` verifica owner token, generación, existencia, expiración y autoridad durable vigente. Ante pérdida o renovación incierta, falla cerrado.

Este contrato es fencing lógico `check-before-commit`. `0.6.0` no declara una transacción física entre esa comprobación y una escritura externa. El Persistence POC debe medir la posible carrera TOCTOU antes de justificar un primitive adicional.

## Cancelación y observabilidad

`JobRuntimeContext.should_stop`, `raise_if_cancelled()` y `wait()` forman el contrato cooperativo. `SIGTERM` despierta `wait()` y conserva la primera razón de detención.

La pérdida de propiedad o un fallo del heartbeat solicita la detención del contexto y evita terminar como éxito. Runtime no mata hilos ni subprocesos de negocio; una dependencia externa debe tener su propio timeout y revisar cancelación entre pasos.

La persistencia local mantiene el contrato de `atlanticus-observability`. Los perfiles Azure son independientes de scheduling y autoridad. Un fallo de bootstrap Azure o del monitor de recursos genera un warning seguro y el job continúa con la traza local cuando corresponde.

## Validación

Desde `backend/`:

```bash
./scripts/validation/check.sh runtime
./scripts/validation/check.sh
```

El cierre coordinado de `0.6.0` requiere además que los consumidores que fijan `atlanticus-job-runtime` regeneren sus `uv.lock` con UV y que los artifacts de procesos vuelvan a transportar el wheel `atlanticus_job_runtime-0.6.0-py3-none-any.whl`.
