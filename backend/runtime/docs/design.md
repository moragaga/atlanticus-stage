# Diseño de `atlanticus-job-runtime`

## Frontera

Runtime orquesta ejecución, coordinación temporal, lease, autoridad y cierre. Observability registra lo ocurrido; cada proceso inyecta su lógica y compone sus conectores. Runtime no conoce ADA, Alarm Engine, Azure Container Apps, Cosmos, SQL, Blob, Service Bus, Databricks, State, Datasets, WAL ni snapshots.

La API pública se limita a `JobDefinition`, `JobRuntimeContext`, `RuntimeConfiguration`, `RuntimeExecutionResult`, `execute_job` y los errores controlados. Lease, scheduling parser, authority store y resource monitor son detalles internos.

R20C consolidó `0.6.0`; R20E consolidó `0.7.0`; R20C.3.2 entrega `0.7.1` corrigiendo starvation del heartbeat frente a mutaciones físicas del mismo owner, sin cambiar la regla de compatibilidad: un consumidor que no configura scheduling y no entrega hooks sigue usando el modelo histórico `RELATIVE`.

## Fuentes de autoridad

Runtime distingue cuatro fuentes y no las mezcla:

- `JobDefinition`: límites cooperativos declarados por código;
- environment efectivo: identidad y metadata operacional de la invocación;
- lease: ownership activo y efímero;
- authority store: generación durable y último scheduled slot completado.

`config.detail.json`, `config.json`, `secrets.detail.json` y `secrets.json` no son entradas directas del Runtime. La plataforma o launcher proyecta al environment únicamente la metadata efectiva requerida.

## Modos de ejecución

### RELATIVE

Es el default. Sin `ATLANTICUS_JOB_SCHEDULE_CRON`, el deadline Runtime parte desde `started_at` y usa `execution_timeout_seconds`.

### SCHEDULED_EXTERNAL

Se activa únicamente con cron efectivo válido. Runtime resuelve el slot actual y el próximo slot desde:

```text
ATLANTICUS_JOB_SCHEDULE_CRON
ATLANTICUS_JOB_SCHEDULE_TIMEZONE
```

Un late start conserva la frontera del slot real; no crea un slot artificial desde el momento de arranque.

`ATLANTICUS_JOB_PLATFORM_TIMEOUT_SECONDS`, cuando existe, agrega un límite físico conocido por la invocación.

### SCHEDULED_RESIDENT

Está aceptado como posibilidad arquitectónica, pero no está implementado ni declarado disponible en `0.7.1`.

## Execution Window

Runtime construye una `ExecutionWindow` como intersección de límites conocidos.

En `RELATIVE`:

```text
runtime_deadline = started_at + execution_timeout_seconds
```

En `SCHEDULED_EXTERNAL`:

```text
runtime_deadline = MIN(
    scheduled_at + execution_timeout_seconds,
    next_scheduled_at,
)
```

Si existe platform timeout:

```text
runtime_deadline = MIN(
    runtime_deadline,
    started_at + platform_timeout_seconds,
)
```

La ventana segura reserva `shutdown_grace_seconds` dentro de esa frontera:

```text
safe_deadline = runtime_deadline - shutdown_grace_seconds
```

Todos los límites son máximos. La finalización natural puede ocurrir antes.

`run_once` es ortogonal: limita la cantidad de iteraciones, no altera schedule, lease ni deadlines.

## Adquisición y autoridad durable

Cada `job_key` posee dos estados separados:

```text
.runtime/leases/<job>.json
.runtime/authority/<job>.json
```

La lease representa ownership activo mediante `owner_token`, `generation`, expiración e identidad operacional. El authority store conserva estado que debe sobrevivir a release, restart, crash y takeover:

```text
generation
last_completed_scheduled_at_utc
```

La adquisición válida avanza `generation` de forma monotónica. El release normal elimina la lease pero no retrocede authority.

En scheduled mode, si `last_completed_scheduled_at_utc` ya cubre el slot actual, la invocación se omite como slot completado. Un slot incompleto no se marca y queda reintentable por una generación posterior.

## Hard authority y renovación

La renovación del heartbeat requiere que el owner siga vigente y que su lease no haya expirado. Una lease propia ya expirada no puede revivirse en la misma generación.

Cuando existe scheduled/platform authority, la expiración renovada queda capped por la frontera efectiva. El heartbeat no puede convertir una invocación vencida en autoridad nueva.

`renew()`, `assert_current()`, takeover y `fenced_mutation()` comparten el mismo fence físico por `job_key`. Una pérdida real de owner, generation o expiración sigue fallando cerrado.

R20C.3.2 separa el comportamiento del heartbeat del wait corto usado por checks/mutaciones síncronas. El heartbeat conserva en memoria la última `expires_at_utc` confirmada bajo fence. Si el fence está temporalmente ocupado, reintenta con `try_acquire()` hasta el menor límite entre esa expiración confirmada y el tiempo monotónico equivalente. El stop del heartbeat interrumpe esa espera. Al conseguir el fence, vuelve a validar owner, generation y expiración antes de escribir una nueva expiración. No existe renovación fuera del fence ni revival de una lease ya expirada.

## Lifecycle

La secuencia estable de `execute_job` es:

1. valida definición, argumentos y configuración;
2. crea `JobRuntimeContext` y su `ExecutionWindow`;
3. crea y adquiere la lease;
4. avanza/bindea `generation` y authority;
5. configura observabilidad;
6. inicia heartbeat;
7. instala el handler cooperativo de `SIGTERM`;
8. ejecuta `recovery(context)` si existe;
9. ejecuta iteraciones mientras exista autoridad y presupuesto;
10. ejecuta `drain(context)` si existe y todavía queda ventana física;
11. detiene heartbeat al entrar en release;
12. marca el scheduled slot completado sólo cuando la ejecución termina con éxito;
13. libera lease y cierra observabilidad.

Los hooks son opt-in. La firma histórica sigue siendo válida:

```text
execute_job(definition=..., iteration=...)
```

El contrato ampliado permite:

```text
execute_job(
    definition=...,
    recovery=...,
    iteration=...,
    drain=...,
)
```

## Semántica de recovery y drain

Recovery, run y drain operan bajo la misma `lease_generation`.

- recovery falla: iteration no comienza y el slot no se completa;
- iteration finaliza normalmente: drain puede ejecutarse antes del release;
- drain falla: el slot scheduled permanece incompleto;
- SIGTERM: solicita stop cooperativo; no realiza I/O desde el signal handler;
- heartbeat: permanece activo durante recovery, run y drain.

Esto permite que el consumidor implemente recuperación y flush final sin transferir al Runtime conocimiento del dominio o del mecanismo de persistencia.

## Fencing authority

`JobRuntimeContext` expone tres elementos públicos para el consumidor:

```text
lease_generation
assert_lease_current()
fenced_mutation()
```

`assert_lease_current()` conserva la comprobación lógica introducida en R20C. Valida ownership, token, generation, existencia y expiración contra la lease y el authority store.

R20E agrega un lock físico por `job_key` bajo `.runtime/fences/`. Adquisición/takeover, renew, release, comprobación de autoridad y `fenced_mutation()` coordinan mediante el mismo lock. La linearization point del takeover queda después de adquirir ese fence: una generación nueva no puede publicarse mientras una mutación de la generación anterior conserve la sección protegida.

`fenced_mutation()` adquiere el fence, revalida la autoridad y entrega al consumidor una sección crítica corta. El consumidor sigue siendo dueño de la operación concreta y el Runtime continúa sin conocer WAL, snapshot, Cosmos, SQL, Storage u otro backend. La abstracción garantiza orden entre writers Atlanticus que usan el mismo `job_key`; no convierte un backend externo en una transacción distribuida ni reemplaza CAS/ETag nativo cuando un recurso lo ofrece.

El hallazgo que origina R20E fue una carrera física reproducible entre `assert_lease_current()` y una mutación posterior: una generación antigua podía superar el check, ocurrir un takeover y luego ejecutar el write. Al hacer que takeover y mutación protegida compartan el mismo fence se elimina esa ventana para el volumen compartido sin introducir semántica de Alarm Engine en Runtime.

En Linux, la implementación usa `flock()` sobre un archivo estable del runtime. En CIFS/SMB, Linux 5.5+ propaga `flock()` mediante locks SMB; el deployment no debe montar con `nobrl`. En filesystem local el lock es cooperativo entre procesos Atlanticus. La sección crítica debe ser breve para no degradar availability ni bloquear el heartbeat innecesariamente.

## Cancelación

`RuntimeCancellationRequested`, `KeyboardInterrupt` y `SIGTERM` generan cierre cooperativo. La primera razón de detención se conserva y `JobRuntimeContext.wait()` puede despertarse inmediatamente.

El agotamiento de la ventana segura y la falta de tiempo para una nueva iteración son cierres normales. Una operación externa bloqueada sigue siendo responsabilidad del consumidor y de los timeouts de su dependencia.

## Recursos y observabilidad

El soporte de recursos permanece separado en modelos, sampler cgroup, detector de presión y monitor. Las muestras sólo actualizan acumuladores en memoria. Una falla de muestreo genera warning y no rompe la lógica del job.

Observability y scheduling son ortogonales. La configuración proyectada a la extensión Azure sigue limitada a sus variables explícitas; Runtime no expone secretos ni serializa configuración de plataforma en logs.

## Compatibilidad y no-alcance de `0.7.1`

R20E no modifica automáticamente la lógica de negocio de ningún proceso consumidor. En particular:

- no agrega cron a procesos existentes;
- no cambia `config.detail.json`, `secrets.detail.json`, `config.json` ni `.env`;
- no cambia deployment Azure;
- no cambia la semántica de `run_once`;
- no agrega lógica de Alarm Engine;
- no implementa WAL ni snapshot;
- no declara `SCHEDULED_RESIDENT` disponible.

El cambio coordinado de consumidores se limita a certificar `atlanticus-job-runtime==0.7.1`, regenerar locks con UV y reconstruir los artifacts que transportan el wheel. Los consumidores existentes pueden seguir usando sólo `assert_lease_current()`; `fenced_mutation()` es opt-in para mutaciones que necesitan la frontera física.
