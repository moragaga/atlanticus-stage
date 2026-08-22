<p align="right">
  <img src="../../docs/assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Atlanticus Datasets

[Volver a Backend](../README.md) · [Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)

`atlanticus-datasets` define el lenguaje neutral con el que Atlanticus identifica datasets,
materializaciones, particiones, partes y resultados de publicación. Permite que aplicaciones y
adapters compartan los mismos contratos sin acoplar la identidad lógica a Pandas, PyArrow,
Parquet, rutas absolutas o un dominio particular.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.0.0` |
| Estado | En revisión |
| Ruta física | `backend/datasets/` |
| Distribución | `atlanticus-datasets` |
| Import público | `atlanticus.datasets` |
| Versión técnica actual | `0.1.0` |
| Python requerido | `3.14.2` |
| Dependencias productivas | Ninguna fuera de la biblioteca estándar |
| Tipo | Wheel tipado de contratos neutrales |

La versión técnica se obtiene de `[project].version` en `backend/datasets/pyproject.toml`. No debe
actualizarse modificando únicamente este README.

## Propósito

El módulo responde dos preguntas:

1. ¿qué unidad lógica intenta publicar o consultar una aplicación?;
2. ¿qué resultado técnico obtuvo esa publicación?

Para ello proporciona:

- identidades estables de datasets;
- definiciones de materializaciones y layouts;
- particiones ordenadas y partes de un file set;
- rutas relativas declarativas separadas de la identidad;
- resultados individuales y resúmenes de lote;
- invariantes para contenido vacío, warnings y fallos compactos.

Datasets es una librería. No declara entrypoints ni ejecuta procesos.

## Límites

Atlanticus Datasets deliberadamente:

- no lee ni escribe archivos;
- no conoce Pandas, PyArrow, Parquet ni extensiones físicas;
- no decide la raíz del volumen o de una aplicación;
- no incluye `application` dentro de `DatasetTarget`;
- no define columnas, schemas tabulares o conversiones de tipos;
- no implementa `replace`, `merge`, scan ni publicación física de partes;
- no crea temporales ni manifiestos;
- no coordina locks, leases o compare-and-swap;
- no conoce catálogos de PI, Dispatch, KPI o ADA;
- no administra watermarks, state, backfill, retención o compactación;
- no convierte nombres ni valores silenciosamente.

`atlanticus-datasets-parquet` implementa la persistencia física sobre estos contratos.
`atlanticus-datasets-runtime` ofrece una fachada tabular sobre el core y el adapter Parquet. La
dependencia inversa no está permitida.

## Posición arquitectónica

```text
atlanticus-datasets
        ↑
atlanticus-datasets-parquet
        ↑
atlanticus-datasets-runtime
        ↑
productores y procesos
```

El core no depende de los dos wheels superiores. El namespace `atlanticus.datasets` utiliza
`pkgutil.extend_path` para que sus extensiones instalables puedan compartir el namespace sin
fusionar sus responsabilidades ni versiones.

## API pública

Solo los veintiún símbolos exportados por `atlanticus.datasets.__init__` forman parte del contrato
público.

| Símbolo | Tipo | Responsabilidad |
|---|---|---|
| `DatasetKey` | Dataclass inmutable | Namespace y nombre estable del dataset. |
| `DatasetDefinition` | Dataclass inmutable | Catálogo de materializaciones y rutas declarativas. |
| `MaterializationDefinition` | Dataclass inmutable | Layout, partición y ruta de una representación. |
| `DatasetPartition` | Dataclass inmutable | Valores de partición conservados en orden canónico. |
| `DatasetTarget` | Dataclass inmutable | Dataset, materialización y partición a confirmar. |
| `DatasetPartKey` | Dataclass inmutable | Parte lógica dentro de un `FileSetLayout`. |
| `DatasetLayout` | Alias de tipo | Unión de los layouts soportados. |
| `SingleArtifactLayout` | Dataclass inmutable | Un artefacto confirmado por target. |
| `FileSetLayout` | Dataclass inmutable | Varias partes identificadas por una dimensión. |
| `DatasetPublicationResult` | Dataclass inmutable | Resultado confirmado o skip de un target. |
| `DatasetPublicationFailure` | Dataclass inmutable | Fallo compacto asociado a un target. |
| `DatasetBatchResult` | Dataclass inmutable | Resultados y fallos independientes de un lote. |
| `PublicationStatus` | `StrEnum` | `committed`, `unchanged` o `skipped`. |
| `PublicationQuality` | `StrEnum` | `success` o `warning`. |
| `PublicationSkipReason` | `StrEnum` | Motivo controlado `empty_content`. |
| `DatasetBatchStatus` | `StrEnum` | `success`, `warning` o `failed`. |
| `DatasetError` | Excepción base | Raíz de errores del módulo. |
| `DatasetValidationError` | Excepción | Valor incompatible con el contrato. |
| `DatasetDefinitionError` | Excepción | Definición lógica inconsistente. |
| `DatasetTargetError` | Excepción | Target o partición incompatible. |
| `__version__` | Texto | Versión expuesta en runtime. |

Las funciones internas de validación no se exportan desde el package raíz.

## 1. Identidad de un dataset

`DatasetKey` contiene un namespace no vacío y un nombre:

```python
from atlanticus.datasets import DatasetKey

key = DatasetKey(
    namespace=('ingestion', 'dispatch'),
    name='truck-events',
)

assert key.identifier == 'ingestion/dispatch/truck-events'
```

Cada segmento admite entre 1 y 120 letras, números, puntos, guiones bajos o guiones, debe comenzar
con una letra o número y no puede ser `.` ni `..`. No se admiten separadores de ruta embebidos.

Las identidades son sensibles a mayúsculas y no se normalizan. Dos textos diferentes siguen siendo
dos identidades aunque un filesystem concreto no distinga su capitalización.

La aplicación no forma parte de `DatasetKey`. La misma definición puede reutilizarse bajo raíces
físicas distintas resueltas por el runtime o por el adapter.

## 2. Materializaciones

Una definición debe contener al menos una materialización:

```python
from atlanticus.datasets import (
    DatasetDefinition,
    FileSetLayout,
    MaterializationDefinition,
    SingleArtifactLayout,
)

definition = DatasetDefinition(
    key=key,
    materializations=(
        MaterializationDefinition(
            name='latest',
            layout=SingleArtifactLayout(),
        ),
        MaterializationDefinition(
            name='operational-week',
            layout=FileSetLayout(part_dimension='shift_id'),
            partition_dimensions=(
                'operational_year',
                'operational_week',
            ),
        ),
    ),
)
```

Una materialización representa una forma publicable del mismo dataset. Su nombre no implica por sí
solo retención, frecuencia o agregación.

### `SingleArtifactLayout`

Representa cada target mediante un artefacto confirmado. Puede utilizarse tanto sin partición como
con una partición histórica.

### `FileSetLayout`

Representa cada target mediante varias partes y exige `part_dimension`. La dimensión de parte no
puede repetirse entre las dimensiones de partición porque resuelven niveles distintos:

- partición: distingue targets independientes;
- parte: distingue fragmentos dentro de un target.

El layout no determina el nombre físico de los archivos ni el mecanismo de commit del conjunto.

## 3. Particiones

Las dimensiones deben comenzar con una letra y admitir solo letras, números o guiones bajos, con un
máximo de 120 caracteres. Los valores admiten entre 1 y 240 letras, números, puntos, guiones bajos
o guiones.

`resolve_target()` ordena el mapping según `partition_dimensions`:

```python
target = definition.resolve_target(
    materialization='operational-week',
    partition={
        'operational_week': 'W30',
        'operational_year': '2026',
    },
)
```

Aunque el mapping llegue en otro orden, la partición resultante conserva:

```text
operational_year=2026/operational_week=W30
```

Se rechazan dimensiones faltantes, adicionales, duplicadas o con valores que no sean strings. Una
materialización sin dimensiones prohíbe recibir partición; una materialización particionada exige
una.

`DatasetPartition.as_dict()` entrega una copia mutable sin alterar la instancia original.

## 4. Target lógico

`DatasetTarget` contiene exclusivamente:

- `dataset`;
- `materialization`;
- `partition`, cuando corresponde.

Su identificador es lógico y comienza con `datasets/`:

```text
datasets/ingestion/dispatch/truck-events/operational-week/
operational_year=2026/operational_week=W30
```

La versión actual no recibe `application` al construir el target. La separación por aplicación,
ambiente o volumen pertenece a la raíz física configurada fuera de este wheel.

`DatasetDefinition.validate_target()` comprueba que el dataset, la materialización y las
dimensiones correspondan exactamente a la definición propietaria.

## 5. Partes de un file set

Solo una materialización con `FileSetLayout` puede resolver partes:

```python
part = definition.resolve_part(
    target=target,
    value='26199001',
)

assert part.identifier.endswith('#shift_id=26199001')
```

`DatasetPartKey` conserva la identidad semántica. No obliga al adapter a utilizar ese valor como
nombre de archivo; un manifiesto físico puede relacionarlo con un nombre opaco.

## 6. Identidad y ruta relativa

La identidad del target y su ubicación declarativa son contratos diferentes.

`DatasetDefinition.route_segments` permite cambiar la ruta relativa base sin cambiar
`DatasetKey`. Cada materialización puede declarar sus propios segmentos. Cuando
`route_segments=None`, se derivan desde la identidad; una tupla vacía en la materialización omite
ese nivel.

```python
definition = DatasetDefinition(
    key=DatasetKey(namespace=('source',), name='logical-name'),
    materializations=(
        MaterializationDefinition(
            name='latest',
            layout=SingleArtifactLayout(),
        ),
    ),
    route_segments=('dispatch', 'std-truck'),
)
```

El target conserva `datasets/source/logical-name/latest`, mientras
`resolve_route_segments(target)` produce `dispatch/std-truck/latest`.

Las rutas son relativas: no aceptan `/`, `\\`, `.` ni `..` como segmentos. El adapter agrega la
raíz física y los nombres de artefactos.

Dentro de una misma definición se rechazan materializaciones que resuelven la misma ruta. El core
no mantiene un registry global que detecte colisiones entre definiciones diferentes.

## 7. Resultado de una publicación

`DatasetPublicationResult` separa el efecto técnico de la calidad del contenido:

| Dimensión | Valores | Significado |
|---|---|---|
| `status` | `committed`, `unchanged`, `skipped` | Efecto sobre la publicación. |
| `quality` | `success`, `warning` | Completitud utilizable del resultado. |

Una publicación confirmada exige:

- `item_count >= 1`;
- `artifact_count >= 1`;
- `size_bytes >= 1` cuando se informa;
- ausencia de `skip_reason`;
- `warning_count=0` para calidad `success`;
- al menos un warning para calidad `warning`.

`finished_at_utc` debe ser timezone-aware. Si llega con otro offset, el modelo lo normaliza a UTC.
`duration_ms` debe ser finito y no negativo. `content_signature`, cuando existe, solo se valida como
texto no vacío; el core no impone algoritmo ni formato.

## 8. Contenido vacío

El único skip controlado es `empty_content`:

```python
from datetime import UTC, datetime

from atlanticus.datasets import DatasetPublicationResult

result = DatasetPublicationResult.skipped_empty(
    target=target,
    finished_at_utc=datetime.now(UTC),
)
```

El resultado exige cero items, cero artefactos, ausencia de bytes y ausencia de firma. Siempre
utiliza calidad `warning`.

Este wheel no puede impedir físicamente una escritura porque no ejecuta I/O. El adapter debe
comprobar el contenido vacío antes de crear directorios, temporales, archivos o manifiestos y luego
representar el resultado mediante `skipped_empty()`.

No existe una opción `allow_empty` en el contrato actual.

## 9. Fallos y lotes

`DatasetPublicationFailure.from_exception()` reduce una excepción a:

- target afectado;
- nombre de la clase de error;
- mensaje fijo `dataset publication failed`;
- duración opcional.

La factory no incorpora el mensaje original de la excepción y evita trasladar URLs, tokens u otros
detalles sensibles al resumen.

`DatasetBatchResult` agrega publicaciones y fallos por target sin simular una transacción global.
Un target solo puede aparecer una vez entre ambas colecciones.

| Condición | `DatasetBatchStatus` |
|---|---|
| Solo resultados sin warnings | `success` |
| Algún warning o mezcla de resultados y fallos | `warning` |
| Fallos y ninguna publicación válida | `failed` |

El batch expone conteos de committed, unchanged, skipped, warnings y fallos. No ejecuta rollback
sobre targets confirmados anteriormente.

## Jerarquía de errores

| Error | También hereda de | Uso |
|---|---|---|
| `DatasetError` | `Exception` | Captura general del módulo. |
| `DatasetValidationError` | `ValueError` | Valor incompatible con el contrato. |
| `DatasetDefinitionError` | `DatasetValidationError` | Definición o ruta inconsistente. |
| `DatasetTargetError` | `DatasetValidationError` | Target, partición o parte incompatible. |

Los mensajes técnicos permanecen en inglés.

## Dependencias y consumidores

El wheel utiliza únicamente la biblioteca estándar. Sus diez consumidores productivos directos
declarados en el snapshot son:

| Capa | Consumidores |
|---|---|
| Backend | `atlanticus-datasets-parquet`, `atlanticus-datasets-runtime` |
| KPI ADA | `ada-kpis-sources` |
| Procesos ADA | `kpis`, `kpis-historian` |
| Data Producers | `fabrica`, `notpii`, `pi`, `remanentes`, `sql` |

Otros módulos pueden recibir sus modelos transitivamente mediante Runtime o un productor, pero eso
no reemplaza una dependencia directa cuando importan `atlanticus.datasets` por sí mismos.

## Source, diseño y mirror pedagógico

```text
backend/datasets/
├── pyproject.toml
├── src/atlanticus/datasets/
│   ├── __init__.py
│   ├── errors.py
│   ├── layouts.py
│   ├── models.py
│   ├── results.py
│   ├── validation.py
│   └── py.typed
├── commented/atlanticus/datasets/
├── docs/design.md
└── tests/
```

El código productivo vive en `src/`. `commented/` conserva el espejo pedagógico en español y queda
fuera del wheel. La prueba de paridad exige los mismos archivos Python y los mismos tokens no
comentarios.

[`docs/design.md`](docs/design.md) profundiza la separación entre identidad, ruta relativa,
atomicidad del target y adapter físico. Fue reconciliado con la versión actual, que ya no incorpora
`application` en `DatasetTarget` y ya dispone de implementaciones Parquet y Runtime.

## Pruebas y validación

El snapshot contiene 30 funciones de prueba que Pytest expande a 35 casos por parametrización:

| Archivo | Contrato validado |
|---|---|
| `test_definitions.py` | Identidades, layouts, materializaciones y rutas. |
| `test_targets.py` | Particiones, targets, partes y rutas personalizadas. |
| `test_results.py` | Publicaciones, vacíos, fallos, lotes y sanitización. |
| `test_datasets_commented_mirror.py` | Archivos y equivalencia source/mirror. |

El gate oficial es `backend/scripts/validation/check.sh datasets --clean`. Aplica Ruff y formato
antes de comprobarlos, ejecuta pruebas, valida `import atlanticus.datasets`, construye el wheel y
verifica su presencia en `backend/dist/`.

| Necesidad | Documento propietario |
|---|---|
| Preparar Python, UV y el workspace | [Primeros pasos y desarrollo](../../docs/development.md) |
| Ejecutar y validar módulos localmente | [Ejecución local](../../docs/local-execution.md) |
| Construir e inspeccionar el wheel | [Empaquetado](../../docs/packaging.md) |
| Actualizar versión y consumidores | [Versionamiento](../../docs/versioning.md) |

## Construcción y contenido del wheel

El resultado sigue el patrón:

```text
backend/dist/atlanticus_datasets-<version>-py3-none-any.whl
```

El wheel debe incluir `atlanticus/datasets/*.py` y `py.typed`. No debe instalar `tests/`,
`commented/`, `docs/` ni caches como packages ejecutables.

El `pyproject.toml` actual declara `readme = "README.md"`. Setuptools incorpora su contenido como
descripción de metadata, pero no incorpora automáticamente los documentos ni imágenes enlazados
fuera del directorio del package. Esta declaración no rompe la instalación del wheel, aunque la
navegación externa puede quedar incompleta al inspeccionar la metadata fuera del monorepo.

## Actualización de versión

Antes de publicar una revisión deben evaluarse como contrato público:

- reglas de identidad, dimensiones y valores;
- forma de materializaciones y layouts;
- semántica de particiones, targets y partes;
- separación entre identidad y rutas relativas;
- estados, calidad y regla de contenido vacío;
- UTC, duración, conteos, bytes y firmas;
- reducción de excepciones y resumen de lotes;
- jerarquía de errores, exports y `__version__`.

Una actualización debe mantener alineados:

1. `[project].version` en `pyproject.toml`;
2. `__version__` en source y mirror;
3. pins exactos y fuentes de los diez consumidores directos;
4. `backend/uv.lock` y locks derivados;
5. pruebas, diseño y README;
6. wheels y artifacts regenerados.

Ruff, formato y pruebas se ejecutan antes y después del cambio conforme a
[Versionamiento](../../docs/versioning.md).

## Elementos no verificados o pendientes

- No se ejecutó el gate durante esta revisión documental porque el entorno disponible no posee
  Python `3.14.2` y no se permite descargarlo automáticamente.
- La declaración `readme = "README.md"` requiere una decisión transversal: conservarla con enlaces
  externos, reemplazarla por una descripción autocontenida o retirarla de la metadata.
- El core no detecta colisiones de rutas relativas entre `DatasetDefinition` diferentes.
- Un `DatasetBatchResult` vacío se resume actualmente como `success`; no existe una prueba que
  confirme si esa semántica es intencional.
- `DatasetPublicationFailure.from_exception()` sanitiza el mensaje, pero el constructor público
  permite entregar manualmente cualquier texto. Un consumidor podría incorporar información
  sensible si evita la factory.
- `content_signature` no exige algoritmo, prefijo ni longitud específicos.
- El contrato de vacío valida el resultado, pero depende del adapter para ejecutar el guard antes
  del I/O.
- No se verificaron colisiones por capitalización en filesystems case-insensitive.

## Control documental

La versión `1.0.0` corresponde únicamente a este README. La versión técnica del wheel continúa
siendo la declarada por su `pyproject.toml`.

El documento permanece **En revisión**. Su aprobación no publica una nueva versión de
`atlanticus-datasets` ni resuelve automáticamente los límites técnicos registrados.

---

[Volver a Backend](../README.md) · [Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)
