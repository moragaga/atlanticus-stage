<p align="right">
  <img src="../../docs/assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Atlanticus Datasets Runtime

[Volver a Backend](../README.md) · [Volver a Datasets](../datasets/README.md) ·
[Ver el adapter Parquet](../datasets-parquet/README.md) ·
[Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)

`atlanticus-datasets-runtime` es la fachada operacional que permite a productores y procesos de
Atlanticus publicar y leer datasets usando `pandas.DataFrame` o `pyarrow.Table`, sin operar
directamente sobre archivos, manifests ni rutas Parquet.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.0.0` |
| Estado | En revisión |
| Ruta física | `backend/datasets-runtime/` |
| Distribución | `atlanticus-datasets-runtime` |
| Import público | `atlanticus.datasets.runtime` |
| Versión técnica actual | `0.2.0` |
| Python requerido | `3.14.2` |
| Dependencias internas | `atlanticus-datasets==0.1.0`, `atlanticus-datasets-parquet==0.2.0` |
| Dependencias tabulares | `pandas==3.0.3`, `pyarrow==25.0.0` |
| Tipo | Wheel tipado de conversión y acceso tabular |

La versión técnica se obtiene de `[project].version` en
`backend/datasets-runtime/pyproject.toml`. No debe actualizarse modificando únicamente este README.

## Propósito

El módulo responde una pregunta concreta:

> ¿Cómo pueden los procesos trabajar con Pandas o PyArrow y conservar una única frontera de
> publicación, lectura, validación y errores sobre el store Parquet de Atlanticus?

Para ello proporciona:

- conversión explícita entre `DataFrame` y `Table`;
- reemplazo y merge de artefactos únicos;
- publicación compuesta de partes;
- lectura completa o proyectada en Arrow o Pandas;
- filtros y selección explícita de targets;
- resultados tipados con métricas físicas;
- traducción de errores físicos a categorías estables de Runtime.

Datasets Runtime es una librería. No declara entrypoint, no inicia aplicaciones y no ejecuta jobs
por sí misma.

## Límites

El wheel deliberadamente:

- no define datasets, layouts o targets funcionales;
- no selecciona qué particiones debe procesar una aplicación;
- no crea el directorio raíz desde variables de entorno;
- no conoce PI, Dispatch, KPI, ADA ni otra solución;
- no transforma datos de negocio ni normaliza timestamps;
- no administra checkpoints, watermarks, reintentos o planificación;
- no mantiene caché de tablas o DataFrames;
- no implementa locks ni coordinación entre escritores;
- no expone el store físico mediante una propiedad pública;
- no ofrece directamente limpieza física ni construcción de rutas.

La aplicación compone la definición, el target y el store. La atomicidad, los archivos y los
manifests pertenecen a `atlanticus-datasets-parquet`; el ciclo de ejecución pertenece a
`atlanticus-job-runtime`.

## Posición arquitectónica

```text
atlanticus-datasets
        ↑
atlanticus-datasets-parquet
        ↑
atlanticus-datasets-runtime
        ↑
productores y procesos ADA
```

Runtime depende de un `ParquetDatasetStore` concreto. Esta decisión es intencional en `0.2.0`:
partes, filtros y resultados todavía utilizan modelos Parquet reales. La inyección evita que la
fachada decida rutas u opciones físicas, pero no representa una independencia que el código no
tiene. Si aparece un segundo backend real, entonces deberá evaluarse un puerto neutral común.

## API pública

Los dieciséis símbolos exportados por `atlanticus.datasets.runtime.__init__` forman el contrato
público:

| Símbolo | Tipo | Responsabilidad |
|---|---|---|
| `DatasetRuntime` | Clase con estado | Convierte formatos y delega lecturas y publicaciones al store. |
| `RuntimeDatasetPart` | Dataclass inmutable | Asocia una clave de parte con un `DataFrame` o una `Table`. |
| `TableReadResult` | Dataclass inmutable | Tabla Arrow, targets y métricas de una lectura. |
| `DataFrameReadResult` | Dataclass inmutable | DataFrame nuevo, targets y métricas de una lectura. |
| `TabularData` | Alias de tipo | Unión pública `pandas.DataFrame | pyarrow.Table`. |
| `to_arrow_table` | Función | Valida y convierte una entrada tabular a Arrow. |
| `to_pandas_dataframe` | Función | Materializa una tabla Arrow como un DataFrame nuevo. |
| `ColumnFilter` | Dataclass reexportada | Declara un filtro físico traducible por Parquet. |
| `FilterOperator` | `StrEnum` reexportado | Define los operadores de filtro soportados. |
| `DatasetRuntimeError` | Excepción base | Raíz de los errores propios de la fachada. |
| `DatasetRuntimeValidationError` | Excepción | Solicitud, layout, target o metadatos inválidos. |
| `DatasetConversionError` | Excepción | Conversión Pandas/Arrow fallida. |
| `DatasetRuntimeReadError` | Excepción | Lectura física fallida. |
| `DatasetRuntimeNotFoundError` | Excepción | Publicación confirmada inexistente. |
| `DatasetRuntimeWriteError` | Excepción | Publicación física fallida. |
| `__version__` | Texto | Versión expuesta en runtime. |

Los helpers internos de `conversion.py`, `facade.py` y `models.py` no deben importarse directamente.

## Construcción e inyección

La raíz física se resuelve en el bootstrap de la aplicación y se entrega al store. Runtime recibe
ese store ya configurado:

```python
from pathlib import Path

from atlanticus.datasets.parquet import ParquetDatasetStore
from atlanticus.datasets.runtime import DatasetRuntime

application_root = Path("/ruta/absoluta/al/volumen/mi-aplicacion")
store = ParquetDatasetStore(root=application_root / "data")
datasets = DatasetRuntime(store=store)
```

El wheel no lee variables de entorno. Si la aplicación usa `VOLUMEN_PATH`, su bootstrap debe
resolverla como ruta absoluta, aplicar el nombre de la aplicación cuando corresponda y construir el
store. La ejecución local y la diferencia con el volumen montado en un contenedor se documentan en
[Ejecución local](../../docs/local-execution.md); la resolución de variables pertenece a
[Configuración](../../docs/configuration.md).

`DatasetRuntime` admite un reloj UTC inyectable para pruebas y resultados omitidos. En producción
normalmente se utiliza el reloj predeterminado.

## Conversión tabular

### De Pandas a Arrow

```python
from atlanticus.datasets.runtime import to_arrow_table

table = to_arrow_table(dataframe)
```

El contrato de conversión:

- acepta únicamente `pandas.DataFrame` o `pyarrow.Table`;
- exige nombres de columna de tipo `str`, no vacíos y sin duplicados;
- no recorta ni normaliza nombres;
- convierte DataFrame con `preserve_index=False`;
- no modifica el objeto recibido;
- elimina sólo el metadato `b"pandas"` creado por esa conversión;
- conserva los demás metadatos de una tabla Arrow recibida;
- retorna exactamente la misma `Table` cuando la entrada ya es Arrow.

Un índice significativo debe convertirse en columna antes de publicar. Pandas y PyArrow pueden
inferir tipos físicos; Runtime no aplica coerciones de negocio para ocultar diferencias de schema.

### De Arrow a Pandas

```python
from atlanticus.datasets.runtime import to_pandas_dataframe

dataframe = to_pandas_dataframe(table)
```

Cada llamada entrega un DataFrame nuevo mediante la conversión convencional de PyArrow. La
representación Pandas de strings, enteros con nulos u otros tipos puede diferir del objeto que
originalmente se publicó. El schema Arrow confirmado continúa siendo la autoridad física.

## Publicar un artefacto único

`replace()` reemplaza completamente la publicación vigente:

```python
result = datasets.replace(
    definition=definition,
    target=target,
    data=dataframe,
)
```

`merge()` combina filas utilizando claves explícitas y un orden opcional:

```python
result = datasets.merge(
    definition=definition,
    target=target,
    data=table,
    key_columns=("timestamp",),
    order_by=("timestamp",),
)
```

Ambas operaciones requieren un `SingleArtifactLayout`. Las claves, las columnas de orden y sus
nulos se validan antes de delegar. El merge físico y su consumo de memoria pertenecen al store
Parquet: la versión actual no implementa un merge incremental o streaming.

## Publicar partes

Un `FileSetLayout` puede recibir partes Pandas y Arrow dentro de una sola publicación:

```python
from atlanticus.datasets.runtime import RuntimeDatasetPart

result = datasets.publish_parts(
    definition=definition,
    target=target,
    parts=(
        RuntimeDatasetPart(key=shift_001, data=dataframe_001),
        RuntimeDatasetPart(key=shift_002, data=table_002),
    ),
    remove_parts=(obsolete_shift,),
)
```

Runtime valida claves, dimensión, duplicados y solapamientos; luego convierte todas las partes
antes de una única llamada al store. La confirmación atómica de la composición continúa
perteneciendo a Parquet y a su manifest `current.json`.

Una operación sólo con eliminaciones es válida. El store impide retirar la última parte confirmada.

## Leer datasets

Los métodos separados mantienen retornos inequívocos:

```python
table_result = datasets.read_table(definition=definition, target=target)
dataframe_result = datasets.read_dataframe(definition=definition, target=target)
schema = datasets.read_schema(definition=definition, target=target)
```

`read_schema()` no materializa las filas, pero hereda la validación física de Parquet. En un file
set confirmado puede inspeccionar y calcular la firma de todas las partes, por lo que no debe
asumirse que siempre sea una operación barata.

Para leer targets explícitos con proyección y filtros:

```python
from atlanticus.datasets.runtime import ColumnFilter, FilterOperator

result = datasets.scan_dataframe(
    definition=definition,
    targets=(day_1, day_2),
    columns=("timestamp", "tonnage"),
    filters=(
        ColumnFilter(
            column="timestamp",
            operator=FilterOperator.GREATER_THAN_OR_EQUAL,
            value=start_utc,
        ),
    ),
)
```

`scan_table()` conserva Arrow y `scan_dataframe()` materializa Pandas. Los targets deben ser
explícitos, no vacíos y sin duplicados. Para varios targets, el store Parquet actual requiere una
proyección explícita de columnas. Los filtros y la proyección se ejecutan antes de convertir a
Pandas.

## Resultados de lectura

`TableReadResult` y `DataFrameReadResult` incluyen:

| Campo | Significado |
|---|---|
| `table` o `dataframe` | Contenido materializado en el formato solicitado. |
| `targets` | Tupla no vacía de targets leídos. |
| `artifact_count` | Cantidad de artefactos físicos inspeccionados. |
| `size_bytes` | Tamaño físico acumulado. |
| `publication_tokens` | Tokens de las publicaciones confirmadas. |
| `warnings` | Advertencias producidas por la lectura física. |
| `target_count` | Propiedad derivada de `targets`. |
| `row_count` | Propiedad derivada del contenido. |

Las colecciones de metadatos deben ser tuplas. Cada lectura Pandas es independiente: Runtime no
conserva ni comparte un DataFrame mutable entre llamadas.

## Semántica de contenido vacío

La definición, el target, el layout y las columnas se validan antes de considerar una entrada
vacía.

| Solicitud válida | Resultado |
|---|---|
| `replace` o `merge` con cero filas | `skipped/empty_content`; no invoca el store. |
| Sin partes entrantes ni eliminaciones | `skipped/empty_content`; no invoca el store. |
| Sólo eliminaciones | Se delega como publicación válida. |
| Alguna parte entrante con cero filas | Se omite la composición completa, incluidas las eliminaciones de esa llamada. |

Un resultado omitido conserva la publicación existente. Su timestamp usa el reloj de Runtime; una
publicación confirmada usa el reloj del store. Ambos relojes deben configurarse de forma coherente
si la aplicación los inyecta.

## Errores

```text
DatasetRuntimeError
├── DatasetRuntimeValidationError
├── DatasetConversionError
├── DatasetRuntimeReadError
│   └── DatasetRuntimeNotFoundError
└── DatasetRuntimeWriteError
```

- definiciones, layouts, targets, filtros y solicitudes inválidas se clasifican como validación;
- entradas imposibles de representar en el formato destino se clasifican como conversión;
- una publicación inexistente se clasifica como `DatasetRuntimeNotFoundError` y también es un
  `FileNotFoundError`;
- corrupción e I/O se clasifican como lectura o escritura;
- la excepción original se conserva mediante encadenamiento.

La conversión a Pandas ocurre después de la lectura física. Actualmente un fallo en esa fase puede
emerger como `DatasetConversionError`, no como `DatasetRuntimeReadError`. Los mensajes propios de
la librería están en inglés; el espejo comentado permanece en español.

## Dependencias y consumidores

La dependencia de Runtime sobre Parquet es directa y pública: `ColumnFilter` y `FilterOperator` se
reexportan desde la fachada. Los consumidores no necesitan importar esos dos tipos desde el adapter
físico.

En el snapshot revisado, la versión `0.2.0` es dependencia directa de:

- `scopes/ada/kpis/sources`;
- `scopes/ada/processes/kpis`;
- `scopes/ada/processes/kpis-historian`;
- los productores `fabrica`, `notpii`, `pi`, `remanentes` y `sql`.

Cambiar su API pública o semántica de vacíos requiere revisar esos ocho consumidores.

## Estructura interna

```text
backend/datasets-runtime/
├── pyproject.toml
├── src/atlanticus/datasets/runtime/
│   ├── __init__.py
│   ├── conversion.py
│   ├── errors.py
│   ├── facade.py
│   ├── models.py
│   └── py.typed
├── commented/atlanticus/datasets/runtime/
├── tests/
├── docs/design.md
└── README.md
```

`src/` es el código distribuido. `commented/` es un espejo pedagógico en español y no forma parte
del wheel. `py.typed` declara soporte de tipado para consumidores.

El [diseño interno](docs/design.md) describe decisiones de implementación. Se conserva como
documento separado y será reconciliado formalmente durante la segunda ronda dedicada a `docs/`;
este README no lo declara todavía como validado.

## Validación

Desde `backend/`, el gate oficial del módulo es:

```bash
./scripts/validation/check.sh datasets-runtime --clean
```

El gate:

1. comprueba el lock compartido;
2. sincroniza el workspace de forma no editable;
3. aplica Ruff fixes y formato a source y mirror;
4. verifica Ruff y formato nuevamente;
5. ejecuta las pruebas del módulo;
6. comprueba el import público;
7. construye un único wheel.

No es una validación pasiva: Ruff puede modificar archivos. Los cambios deben revisarse y el gate
debe repetirse hasta quedar estable antes de publicar una versión.

La suite actual contiene 32 funciones físicas y 39 casos lógicos después de parametrización. Cubre
conversiones, contrato público, mirror, resultados, lecturas, publicaciones y POC de merge PI y
partes mixtas Dispatch.

Los procedimientos transversales se mantienen en:

| Necesidad | Guía propietaria |
|---|---|
| Instalar UV, Python y sincronizar | [Primeros pasos y desarrollo](../../docs/development.md) |
| Ejecutar localmente una aplicación | [Ejecución local](../../docs/local-execution.md) |
| Construir e inspeccionar el wheel | [Empaquetado](../../docs/packaging.md) |
| Elegir y propagar una versión | [Versionamiento](../../docs/versioning.md) |
| Resolver variables y secretos | [Configuración](../../docs/configuration.md) |

## Construcción y versionamiento

El wheel se construye mediante el gate oficial y se deposita en `backend/dist/`. El nombre y la
versión efectivos deben comprobarse contra `pyproject.toml`; el README incluido en la metadata del
wheel debe poder interpretarse sin depender de anexos inexistentes en la distribución.

Antes de publicar una nueva versión deben revisarse:

- compatibilidad de los dieciséis exports públicos;
- cambios en conversiones, vacíos y clasificación de errores;
- compatibilidad con las versiones exactas de Datasets y Datasets Parquet;
- source y mirror comentado;
- los ocho consumidores directos;
- resultado de Ruff, pruebas, import y construcción del wheel.

El criterio semántico y el procedimiento completo pertenecen a
[Versionamiento](../../docs/versioning.md).

## Decisiones pendientes

- Ejecutar el gate con Python `3.14.2`; no se ejecutó durante esta modificación documental porque
  esa versión no está instalada en el entorno de revisión.
- Definir transversalmente cómo se empaquetarán los enlaces y anexos de los README declarados en
  `pyproject.toml`, para que la descripción larga del wheel no apunte a recursos ausentes.
- Mantener la dependencia concreta con Parquet mientras no exista un segundo backend real; sólo
  entonces evaluar un puerto neutral.
- Decidir si Runtime debe exponer una operación acotada de cleanup. Hoy el store está oculto y la
  fachada no ofrece esa capacidad.
- Medir y documentar límites de memoria del merge heredado de Parquet.
- Medir el costo de hashing de `read_schema()` sobre file sets grandes.
- Decidir si los fallos de conversión posteriores a una lectura deben conservar
  `DatasetConversionError` o traducirse a `DatasetRuntimeReadError`.
- Garantizar que el reloj de Runtime y el del store sean coherentes cuando ambos se inyecten.
- Confirmar si una parte entrante vacía debe omitir también las eliminaciones solicitadas en la
  misma llamada; ése es el comportamiento actual.
- Revisar si la captura genérica de excepciones del store puede ocultar errores de programación de
  futuras subclases.
- Revisar `docs/design.md` en la segunda ronda, especialmente la referencia al futuro
  `RuntimeDatasetContext` y la descripción del costo de `read_schema()`.

## Control documental

La versión `1.0.0` corresponde únicamente a este README. No modifica la versión técnica del wheel.

El documento se encuentra **En revisión**. Para declararlo validado deben aprobarse su contenido y
pendientes, y ejecutarse el gate técnico cuando el entorno disponga de Python `3.14.2`.

---

[Volver a Backend](../README.md) · [Volver a Datasets](../datasets/README.md) ·
[Ver el adapter Parquet](../datasets-parquet/README.md) ·
[Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)
