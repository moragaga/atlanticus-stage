# Atlanticus Observability

`atlanticus-observability==0.5.0` define la trazabilidad neutral de procesos Atlanticus. Incluye
eventos, contexto heredable, medición de ejecución e iteraciones, protección de contratos externos,
persistencia diaria local y puertos neutrales para tracing.

No ejecuta jobs, no crea hilos de negocio, no mide recursos, no conoce datasets ni contiene un SDK
de Azure. El sink persistente recibe una raíz de volumen ya resuelta. Job Runtime realiza el
muestreo de recursos y emite eventos neutrales. El adaptador Azure es un wheel separado que aplica
una proyección mucho más acotada.

## Requisitos

- CPython `3.14.2`;
- `atlanticus-kernel==0.1.0`;

## Inicio sobre el volumen

```python
import os

from atlanticus.observability import (
    ObservabilitySettings,
    configure_volume_observability,
    trace_execution,
    trace_iteration,
)

settings = ObservabilitySettings.build(
    application='ada',
    service='dispatch-ingestion-job',
    module='dispatch_ingestion',
    environment='local',
    volume_path=os.environ['VOLUMEN_PATH'],
)
configure_volume_observability(settings=settings)

with trace_execution():
    with trace_iteration(1):
        # trabajo del proceso
        pass
```

## Contratos externos

```python
from atlanticus.observability import ResultSummary, runtime_guard


@runtime_guard(
    operation='cosmos.query',
    component='cosmos',
    target_alias='operational-read',
    result_mapper=lambda rows: ResultSummary(
        metrics={'record_count': len(rows)},
    ),
)
def load_rows(container, query):
    return list(container.query_items(query=query, enable_cross_partition_query=True))
```

Los parámetros y resultados no se inspeccionan automáticamente. Los mappers declaran únicamente
las cantidades y atributos seguros que se deben trazar. La función decorada retorna el mismo valor
y propaga la misma excepción original, incluso si falla un mapper de observabilidad.

## Cantidades de datos

```python
from atlanticus.observability import EventAudience, emit_data_event

emit_data_event(
    'data.downloaded',
    audience=EventAudience.OPERATIONS,
    record_count=1250,
    byte_count=94231,
    file_count=2,
    duration_ms=830.4,
    attributes={'source_kind': 'cosmos'},
)
```

No se incluyen connection strings, consultas completas ni contenido de filas o archivos.
`EventAudience.OPERATIONS` es una decisión explícita y neutral que permite a una extensión de nube
seleccionar el evento. El valor predeterminado `LOCAL` conserva el detalle sólo en la traza local.

## Traza diaria

El sink ubica observabilidad dentro del scope de la aplicación. La ruta se segmenta únicamente por
servicio y día UTC porque cada ambiente utiliza su propio storage:

```text
${VOLUMEN_PATH}/
└── ada/
    └── logs/
        └── dispatch-ingestion-job/
            ├── latest.json
            └── day=2026-07-17/
                ├── executions.jsonl
                ├── iterations.jsonl
                ├── issues.jsonl
                └── daily-summary.json
```

- `executions.jsonl` conserva los cierres terminales de ejecuciones durante el día;
- `iterations.jsonl` conserva los resúmenes terminales de iteraciones con trabajo;
- `issues.jsonl` conserva advertencias y errores con diagnóstico local acotado;
- `daily-summary.json` acumula el servicio completo aunque cambie el contenedor;
- `latest.json` mantiene el último estado terminal de ejecución en la raíz del servicio;
- al activarse un día se purgan los directorios de semanas ISO anteriores y se conserva la semana
  UTC vigente.

`run_id` correlaciona el detalle de cada proceso dentro del historial del servicio. `instance_id`,
`process_id`, `module` y `environment` también permanecen dentro de cada registro y no agregan más
niveles. JSONL usa append bajo lock de hilo y una única escritura por registro. Los snapshots se
escriben en un temporal del mismo filesystem, se sincronizan y se reemplazan con `os.replace`.

La misma aplicación puede crear otros dominios hermanos fuera de `logs`. Observability nunca
escribe ni purga esos espacios.

La persistencia asume un único escritor activo por `application + service`. Job Runtime garantiza
esa condición antes de configurar este sink; observability no administra locks de
ejecución ni interpreta timeouts de Azure.

## Local y despliegue

Cada sink recibe el evento neutral y aplica su política. `FullEventProjection` conserva todo para la
traza local. `FilteredEventProjection` puede seleccionar severidad, categorías o nombres y omitir
atributos, métricas, traceback y eventos de recursos. `TraceBridge` permite spans manuales sin que
este wheel dependa de OpenTelemetry. Este wheel no realiza envíos de red.

## Concurrencia

`ExecutionContext` ya reserva `concurrency_scope`, `task_id`, `worker_kind`, `worker_index`,
`target_alias` y `concurrency_group`. El job específico decide si usa hilos o procesos; Job Runtime
ofrece mecanismos compartidos y propagación de contexto. El package no implementa ejecutores ni
comportamiento especial para un conector concreto.

## Validación y wheel

Desde `backend/`:

```bash
uv sync --locked --all-packages --group dev
uv run --all-packages pytest observability/tests
uv run --all-packages ruff check observability
uv run --all-packages ruff format --check observability
uv build --package atlanticus-observability --out-dir dist
```

Las pruebas unitarias son propias del wheel. `atlanticus-testing` permanece para la etapa final y no
es una dependencia de esta versión.
