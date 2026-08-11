# Atlanticus Datasets Runtime

`atlanticus-datasets-runtime==0.1.0` es la fachada operacional bidireccional para los datasets de
Atlanticus. Permite que fuentes, KPI y alarmas trabajen con `pandas.DataFrame` o `pyarrow.Table`
sin utilizar directamente la API física de Parquet.

```text
Adaptadores / KPI / Alarmas
            ↓
   atlanticus-datasets-runtime
       ↓                 ↓
Pandas ↔ PyArrow    contratos datasets
            ↓
 atlanticus-datasets-parquet
            ↓
      store Parquet
```

El store se construye fuera del package y se inyecta:

```python
from pathlib import Path

from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime

parquet_store = ParquetDatasetStore(root=Path('/app/volumen/data'))
dataset_runtime = DatasetRuntime(store=parquet_store)
```

La fachada depende de `datasets-parquet==0.1.0` porque esa versión expone modelos concretos para
partes, filtros y resultados de lectura. `DatasetRuntime` recibe explícitamente un
`ParquetDatasetStore`; la inyección evita que la fachada decida rutas u opciones físicas, pero no
simula independencia de una API con la que debe ser realmente compatible. En pruebas pueden usarse
subclases controladas del store. Si se incorpora un segundo backend físico, corresponderá extraer
un puerto neutral común en una versión posterior.

El store queda encapsulado: no existe una propiedad pública para accederlo desde consumidores. Las
lecturas y publicaciones deben pasar por la fachada para mantener una única clasificación de
validaciones y errores.

## Escritura

`replace()` y `merge()` aceptan Pandas o Arrow:

```python
result = dataset_runtime.replace(
    definition=definition,
    target=target,
    data=dataframe,
)

result = dataset_runtime.merge(
    definition=definition,
    target=target,
    data=arrow_table,
    key_columns=('timestamp',),
    order_by=('timestamp',),
)
```

Las reglas de conversión son explícitas:

- `DataFrame` se convierte con `preserve_index=False`;
- un índice significativo debe convertirse en columna antes de publicar;
- el runtime nunca modifica el `DataFrame` recibido;
- una `pa.Table` atraviesa la frontera sin conversión ni copia innecesaria;
- se retira sólo el metadato privado generado por Pandas al crear Arrow;
- no se convierten silenciosamente tipos ni nombres de columnas;
- PyArrow continúa siendo el schema físico autoritativo.

## Lectura

Los métodos separados mantienen retornos inequívocos para el tipado:

```python
table_result = dataset_runtime.read_table(
    definition=definition,
    target=target,
)

dataframe_result = dataset_runtime.read_dataframe(
    definition=definition,
    target=target,
)
```

También existen `scan_table()` y `scan_dataframe()` para targets explícitos, proyección y filtros:

```python
from atlanticus.datasets.runtime import ColumnFilter, FilterOperator

result = dataset_runtime.scan_dataframe(
    definition=definition,
    targets=(day_1, day_2),
    columns=('timestamp', 'tonnage'),
    filters=(
        ColumnFilter(
            column='timestamp',
            operator=FilterOperator.GREATER_THAN_OR_EQUAL,
            value=start_utc,
        ),
    ),
)
```

La proyección y los filtros se ejecutan en Parquet antes de convertir a Pandas. Cada lectura Pandas
entrega un `DataFrame` nuevo. El runtime no conserva caché ni comparte un objeto mutable global.

## Partes

Una publicación compuesta puede mezclar ambos formatos:

```python
from atlanticus.datasets.runtime import RuntimeDatasetPart

result = dataset_runtime.publish_parts(
    definition=definition,
    target=target,
    parts=(
        RuntimeDatasetPart(key=shift_001, data=dataframe_001),
        RuntimeDatasetPart(key=shift_002, data=table_002),
    ),
)
```

Todas las partes se validan y convierten antes de llamar una única vez al store. La atomicidad de
la composición continúa perteneciendo a `datasets-parquet` y su `current.json`.

## Vacíos y errores

Una entrada con cero filas produce `skipped/empty_content`, no invoca el store y conserva la
publicación vigente. Antes de aplicar ese atajo se validan la definición, el target y el layout, de
modo que una solicitud inválida nunca pueda aparentar un `skipped` correcto. Una solicitud sin
partes ni eliminaciones también se considera vacía. Las eliminaciones explícitas siguen siendo
operaciones válidas aunque no incluyan partes entrantes.

Los fallos reales levantan excepciones tipadas y conservan su causa:

```text
DatasetRuntimeError
├── DatasetRuntimeValidationError
├── DatasetConversionError
├── DatasetRuntimeReadError
│   └── DatasetRuntimeNotFoundError
└── DatasetRuntimeWriteError
```

Una publicación inexistente se informa mediante `DatasetRuntimeNotFoundError`. Los layouts,
schemas, filtros y solicitudes inválidas se informan como `DatasetRuntimeValidationError`; los
fallos físicos restantes conservan las categorías de lectura o escritura.

Los mensajes de error están en inglés; las definiciones y el espejo comentado permanecen en
español.

## Fuera de alcance

Esta versión no incorpora:

- conexiones a PI, SQL, Dispatch, Blob o APIs;
- transformaciones de negocio, pivots o normalización de timestamps;
- selección o descubrimiento de targets por scope;
- checkpoints o watermarks de fuentes;
- planificación, reintentos, locks o paralelismo;
- caché de tablas o DataFrames;
- schemas globales de PI, Dispatch o ADA.

## Validación

Desde `backend/`:

```bash
uv run --no-editable --all-packages pytest datasets-runtime/tests
uv build --package atlanticus-datasets-runtime --out-dir dist
```

Las pruebas incluyen conversión bidireccional, índices, nulos, timezone, vacíos, errores, partes
mixtas y composición real con `atlanticus-datasets-parquet`, además de POC para PI y Dispatch.
