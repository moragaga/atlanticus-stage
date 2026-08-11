# `atlanticus-state`

`atlanticus-state==0.1.0` conserva memoria técnica compacta entre ejecuciones de jobs. El package
no conoce PI, Dispatch, KPI, alarmas ni conectores: el job decide la clave y el valor que necesita
recordar.

## Contrato principal

Cada `StateKey` representa un único documento actual dentro del scope de la aplicación:

```text
${VOLUMEN_PATH}/
└── ada/
    └── .runtime/
        └── state/
            └── ingestion/
                └── pi/
                    └── state/
                        └── publication.json
```

```python
from atlanticus.state import AtomicStateStore, StateKey

store = AtomicStateStore(volume_path='/app/volumen', application='ada')
publication = StateKey(
    namespace=('ingestion', 'pi', 'state'),
    name='publication',
)

previous = store.read(publication)
current = store.replace(
    publication,
    {
        'source_watermark': '2026-07-20T12:30:00Z',
        'change_token': 'sha256:...',
        'quality_status': 'warning',
        'warning_count': 3,
    },
)
```

El archivo contiene únicamente el sobre técnico y el valor entregado por el dueño del dominio:

```json
{
  "schema_version": 1,
  "updated_at_utc": "2026-07-20T12:31:04.120000Z",
  "value": {
    "change_token": "sha256:...",
    "quality_status": "warning",
    "source_watermark": "2026-07-20T12:30:00Z",
    "warning_count": 3
  }
}
```

No existe un identificador incremental ni historial de runs. Una clave conserva siempre el último
valor confirmado y su tamaño predeterminado está limitado a 1 MiB.

## Atomicidad y calidad de datos

Atomicidad significa que el documento completo quedó escrito, sincronizado y reemplazado. No
significa que la fuente haya entregado todas las columnas, tags, horas o elementos esperados.

Un resultado parcial pero utilizable debe publicar un nuevo estado con `quality_status=warning` y
permitir que continúen KPI, alarmas y deliveries. Los detalles extensos pertenecen a pipeline
control y observability. Sólo un fallo técnico que impide confirmar o interpretar el artefacto
debe bloquear el avance del estado.

Si una escritura falla, el documento confirmado anteriormente permanece intacto. Una terminación
forzosa puede dejar el temporal porque no ejecuta `finally`; la siguiente escritura de esa misma
clave elimina únicamente temporales propios con UUID antes de confirmar el valor nuevo y emite
`state.temporary.recovered`. Los errores de lectura, corrupción, schema, tamaño, limpieza y
escritura se propagan al job. Un fallo del backend de observabilidad, en cambio, no cambia el
resultado funcional ni hace que una escritura ya confirmada parezca fallida.

## Detección de cambios

`build_state_signature()` calcula SHA-256 sobre JSON canónico. El resultado no cambia por el orden
de las claves y evita introducir contadores cuyo único significado sería la cantidad histórica de
ejecuciones.

```python
from atlanticus.state import build_state_signature

change_token = build_state_signature(
    {
        'pi_watermark': '2026-07-20T12:30:00Z',
        'dispatch_watermark': '2026-07-20T12:25:00Z',
    }
)
```

## Deduplicación temporal

`ExpiringKeySet` cubre casos como mensajes de Service Bus. Guarda sólo hashes SHA-256, exige TTL y
capacidad máxima explícitos, purga expirados y procesa lotes con una lectura y una escritura.

```python
from atlanticus.state import ExpiringKeySet

messages = ExpiringKeySet(
    store=store,
    key=StateKey(
        namespace=('ingestion', 'service-bus', 'state'),
        name='messages',
    ),
    retention_seconds=4 * 60 * 60,
    max_entries=10_000,
)

already_seen = messages.contains_many(message_ids)
messages.add_many(processed_message_ids)
```

El TTL del ejemplo pertenece al job; la biblioteca no impone cuatro horas ni otra retención de
negocio.

## Observabilidad

Las lecturas, escrituras y purgas correctas generan eventos locales con duración, bytes o
cantidades. Warnings, errores y la recuperación de temporales huérfanos se marcan para operaciones
y por eso entran al perfil Azure `slim`. Nunca se emite el contenido de `value` ni los
identificadores usados para deduplicar.

Las lecturas cargan como máximo el límite configurado más un byte. El parser exige UTF-8, rechaza
claves duplicadas, números no finitos y profundidad excesiva. `StateDocument` conserva un snapshot
profundamente inmutable, aunque el consumidor modifique después el objeto original.

El diseño completo está en [`docs/design.md`](docs/design.md).
