# Atlanticus Datasets Parquet

`atlanticus-datasets-parquet==0.1.0` implementa la persistencia física local de los contratos de
`atlanticus-datasets==0.1.0`. La API pública recibe y retorna `pyarrow.Table`; no depende de Pandas,
no interpreta fuentes y no decide scopes operacionales.

## Alcance

- reemplazo atómico de un único `data.parquet`;
- merge Arrow por claves con reemplazo completo de la fila entrante;
- publicaciones compuestas mediante partes inmutables y `current.json`;
- lectura completa del target vigente;
- proyección y filtros con predicate pushdown Parquet;
- lectura de varias particiones explícitas;
- limpieza de temporales y partes huérfanas con gracia;
- compresión Zstandard nivel 3 y row groups configurables.

Quedan fuera el parseo de PI o Dispatch, las zonas horarias, los pivots, la retención operacional,
el descubrimiento de particiones, Blob Storage y la conversión a DataFrame.

## Store

```python
from pathlib import Path

from atlanticus.datasets.parquet import ParquetDatasetStore

store = ParquetDatasetStore(root=Path('/app/volumen/data'))
```

La ruta física se obtiene anexando a la raíz los segmentos validados por
`DatasetDefinition.resolve_route_segments()`. El store no acepta rutas libres aportadas en cada
operación ni recorre carpetas para descubrir publicaciones.

## Reemplazo

```python
result = store.replace(
    definition=pi_definition,
    target=day_target,
    table=arrow_table,
)
```

Un contenido vacío retorna `skipped/empty_content` antes de crear la carpeta. Para un contenido
válido se escribe un temporal en el mismo directorio, se fuerza a disco, se reabre y valida, y sólo
entonces se confirma con `os.replace()`.

El store no ejecuta `chmod` ni intenta corregir permisos del host. Los Parquet, temporales y
manifiestos conservan los permisos resultantes del usuario y el `umask` del proceso. La preparación
de un bind mount para WSL o Docker pertenece exclusivamente al script local de runtime.

## Merge

```python
result = store.merge(
    definition=pi_definition,
    target=day_target,
    incoming=incremental_table,
    key_columns=('timestamp',),
    order_by=('timestamp',),
)
```

Reglas:

- el schema entrante es la autoridad;
- una columna nueva queda nula en filas históricas;
- una columna ausente del nuevo schema desaparece de la publicación final;
- ante una clave repetida gana la última fila entrante completa;
- un nulo entrante reemplaza un valor anterior;
- las claves no pueden faltar ni contener nulos;
- no se realizan coerciones silenciosas de tipos.

## Partes planas

Una materialización compuesta conserva todos los archivos en el directorio del target:

```text
operational-day/year=2026/month=07/day=21/
├── current.json
├── shift_id=26199001--<sha256>.parquet
└── shift_id=26199002--<sha256>.parquet
```

```python
from atlanticus.datasets.parquet import ParquetPart

result = store.publish_parts(
    definition=dispatch_definition,
    target=day_target,
    incoming_parts=(
        ParquetPart(key=shift_001, table=table_001),
        ParquetPart(key=shift_002, table=table_002),
    ),
)
```

Cada parte entrante es el contenido completo de su identidad. Las partes no mencionadas se
conservan y sólo se retiran mediante `remove_parts`. Si una parte entrante está vacía, se omite la
publicación completa. `current.json` se reemplaza sólo después de validar todos los archivos nuevos.

Los nombres incorporan identidad lógica y hash de contenido. El manifiesto es la única autoridad:
los lectores nunca incorporan archivos adicionales aunque parezcan Parquet válidos.

## Lectura

`read()` retorna el target completo vigente:

```python
result = store.read(definition=definition, target=target)
table = result.table
```

`scan()` recibe targets explícitos, proyección y filtros combinados mediante `AND`:

```python
from atlanticus.datasets.parquet import ColumnFilter, FilterOperator

result = store.scan(
    definition=definition,
    targets=(day_1, day_2, day_3),
    columns=('timestamp', 'tonnage'),
    filters=(
        ColumnFilter('timestamp', FilterOperator.GREATER_THAN_OR_EQUAL, start_utc),
        ColumnFilter('timestamp', FilterOperator.LESS_THAN, end_utc),
    ),
)
```

Cuando se leen varios targets, `columns` es obligatorio. Una columna solicitada que existe sólo en
particiones nuevas se completa con nulos en las anteriores; tipos incompatibles producen error. Un
filtro `eq` o `in` sobre la dimensión de parte selecciona los archivos antes de abrirlos.

La distribución de responsabilidades es:

| Capa | Responsabilidad |
|---|---|
| Consumidor KPI/Alarmas | Declara scope, columnas y filtros requeridos |
| DatasetContext futuro | Traduce el scope a `DatasetTarget` explícitos |
| Datasets Parquet | Itera, abre, filtra, proyecta y concatena los archivos confirmados |
| Regla de negocio | Convierte a Pandas si lo necesita y realiza el cálculo |

## Huérfanos

Una parte o temporal no confirmado permanece invisible. `cleanup()` elimina únicamente artefactos
propios no referenciados después de la gracia predeterminada de diez minutos. Cada escritura intenta
la misma limpieza antes de comenzar. Los huérfanos nunca se leen, mezclan, deduplican ni recuperan.

## Validación

Desde `backend/`:

```bash
uv run --no-editable --all-packages pytest datasets-parquet/tests
uv build --package atlanticus-datasets-parquet --out-dir dist
```

Las pruebas de concepto cubren PI wide incremental y Dispatch por `shift_id`, además de fallos antes
del reemplazo del Parquet o del manifiesto.

La aceptación con procesos reales vive separada en `backend/runtime/docker`. Después de construir
los wheels se ejecuta individualmente con:

```bash
bash runtime/docker/08_datasets_parquet_runtime_test.sh
```
