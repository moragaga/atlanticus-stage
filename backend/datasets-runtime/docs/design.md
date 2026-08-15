# Diseño de `atlanticus-datasets-runtime` 0.2.0

## Responsabilidad

El runtime es una fachada de acceso tabular, no un orquestador de ingesta. Recibe datos ya
preparados, los valida, los convierte a Arrow y delega su publicación. En lectura conserva Arrow o
materializa Pandas después de que el store resolvió targets, partes, columnas y filtros.

| Responsabilidad | Dueño |
|---|---|
| Identidad, layouts, targets y resultados de publicación | `atlanticus-datasets` |
| Archivos, manifiestos, merge, filtros y atomicidad | `atlanticus-datasets-parquet` |
| Conversión Pandas/Arrow y API pública bidireccional | `atlanticus-datasets-runtime` |
| Consulta, transformación y checkpoint de fuente | Adaptador de la fuente |
| Scope y unión de requerimientos de una ejecución | Futuro `RuntimeDatasetContext` |
| Planificación, lease, timeout y estado del job | `atlanticus-job-runtime` |

## Dependencia física explícita

`datasets-parquet==0.2.0` utiliza `ParquetPart`, `ColumnFilter` y `ParquetReadResult` como tipos
concretos. Un protocolo que ocultara esa realidad no sería estructuralmente compatible para las
partes ni los filtros. Por eso esta versión depende explícitamente del adaptador, aunque el objeto
store continúe llegando por inyección.

`DatasetRuntime` exige un `ParquetDatasetStore` y no expone el store mediante una propiedad pública.
La decisión elimina un protocolo prematuro sin otro backend real e impide que los consumidores se
salten la clasificación de errores de la fachada. La aplicación sólo importa `ColumnFilter` y
`FilterOperator` desde `atlanticus.datasets.runtime`; los demás tipos físicos no se propagan por sus
módulos.

## Conversión a Arrow

Un `DataFrame` se valida antes de llamar a `pa.Table.from_pandas()` para impedir normalizaciones
accidentales de nombres. El índice se descarta siempre. Después se elimina la clave `b'pandas'` del
schema porque describe cómo reconstruir el objeto fuente y no forma parte del contrato físico.
Otros metadatos Arrow se conservan.

No se alinean ni convierten tipos entre partes. Dos tablas mixtas son compatibles sólo si sus
schemas Arrow resultantes coinciden, como exige el store. Una diferencia `string`/`large_string`,
`int32`/`int64` o timezone continúa siendo un error visible que debe resolver el adaptador dueño.

## Conversión a Pandas

`table.to_pandas()` se ejecuta sin `ArrowDtype` global. El DataFrame resultante usa la representación
convencional de la versión instalada de Pandas. Por ello un entero con nulos, un string o un valor
ausente puede tener una representación distinta del objeto original. El schema Arrow, no el dtype
reconstruido, es la autoridad persistida.

La conversión se realiza sólo después de la lectura proyectada. El runtime no conserva referencias
a DataFrames entregados ni reutiliza resultados entre llamadas.

Cuando el consumidor necesita únicamente conocer las columnas/tipos confirmados de un target,
`DatasetRuntime.read_schema()` delega al store Parquet y devuelve el `pa.Schema` sin leer las filas.
Los errores mantienen la misma clasificación pública de las demás lecturas.

## Vacíos

El contrato lógico se valida antes de aplicar el guard y antes de invocar el store:

```text
validar definición, target y layout
        │
        └── convertir y validar contenido
                    │
                    ├── cero filas → skipped/empty_content
                    │                sin llamada al store
                    └── contenido  → delegar publicación
```

En un file set, una sola parte vacía omite la composición completa. Una eliminación explícita no es
contenido vacío y puede ejecutarse sin partes entrantes. El store impide retirar la última parte
porque una publicación confirmada no puede quedar vacía.

## Errores

La fachada valida tipos tabulares, columnas, claves de merge, pertenencia de targets, compatibilidad
del layout, targets repetidos y partes duplicadas. Las validaciones adicionales del store, incluidos
schemas y filtros físicos, se traducen a `DatasetRuntimeValidationError`. Una publicación ausente se
traduce a `DatasetRuntimeNotFoundError`. Corrupción e I/O conservan errores de lectura o escritura
con `raise ... from error`; nunca se devuelve un estado `failed` silencioso.

Los resultados de lectura exigen tuplas para targets, tokens y warnings. Esto evita introducir
colecciones mutables dentro de dataclasses congeladas.

## Concurrencia y caché

No existen locks ni caché propios. PyArrow y Pandas sólo viven durante la llamada. La coordinación
de un escritor por target pertenece al runtime del job y el commit atómico pertenece a Parquet.
Los lectores siempre observan una publicación ya confirmada por el store.
