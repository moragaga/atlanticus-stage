<p align="right">
  <img src="../../docs/assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Atlanticus Datasets Parquet

[Volver a Backend](../README.md) · [Volver a Datasets](../datasets/README.md) ·
[Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)

`atlanticus-datasets-parquet` adapta los contratos neutrales de `atlanticus-datasets` a
publicaciones locales basadas en PyArrow y archivos Parquet. Implementa rutas físicas, escritura
atómica, lectura proyectada, filtros, manifests para file sets y limpieza de artefactos propios,
sin incorporar reglas de PI, Dispatch, KPI, ADA u otra aplicación.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.0.0` |
| Estado | En revisión |
| Ruta física | `backend/datasets-parquet/` |
| Distribución | `atlanticus-datasets-parquet` |
| Import público | `atlanticus.datasets.parquet` |
| Versión técnica actual | `0.2.0` |
| Python requerido | `3.14.2` |
| Dependencias productivas | `atlanticus-datasets==0.1.0`, `pyarrow==25.0.0` |
| Tipo | Wheel tipado de persistencia física local |

La versión técnica se obtiene de `[project].version` en
`backend/datasets-parquet/pyproject.toml`. No debe actualizarse modificando únicamente este README.

## Propósito

El módulo responde una pregunta concreta:

> ¿Cómo convertir un `DatasetTarget` validado y una tabla Arrow en una publicación Parquet local
> confirmada, legible e independiente de la lógica de negocio?

Para ello proporciona:

- reemplazo atómico de un artefacto único;
- merge por claves con autoridad del schema entrante;
- publicación incremental de partes mediante un manifest confirmado;
- lectura completa, proyección y filtros Parquet;
- lectura explícita de varios targets;
- validación de schemas, conteos, tamaños y firmas;
- limpieza limitada de temporales y partes huérfanas propias.

Datasets Parquet es una librería. No declara entrypoints, no inicia jobs y no decide qué targets debe
procesar una aplicación.

## Límites

El wheel deliberadamente:

- no interpreta Pandas ni retorna `DataFrame`;
- no normaliza nombres de columnas, timestamps, sentinelas o tipos de negocio;
- no descubre particiones recorriendo el filesystem;
- no decide la raíz de `VOLUMEN_PATH`, la aplicación ni el ambiente;
- no define catálogos de datasets o scopes funcionales;
- no descarga, transforma ni calcula datos;
- no administra watermarks, state, backfill, retención o historia de versiones;
- no implementa locks distribuidos entre procesos o contenedores;
- no soporta Blob Storage ni object storage;
- no ejecuta `chmod` ni corrige permisos del host;
- no garantiza atomicidad sobre filesystems remotos con semánticas diferentes a un filesystem
  local.

Las conversiones tabulares y la fachada de uso pertenecen a `atlanticus-datasets-runtime`. La
selección de targets pertenece al consumidor o a una capa superior que conozca el scope.

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

Datasets Parquet depende del contrato neutral y de PyArrow. No importa Runtime ni aplicaciones. El
namespace `atlanticus.datasets` se comparte mediante packages de namespace, pero cada wheel
mantiene su propia responsabilidad y versión.

## API pública

Los dieciséis símbolos exportados por `atlanticus.datasets.parquet.__init__` forman el contrato
público:

| Símbolo | Tipo | Responsabilidad |
|---|---|---|
| `ParquetDatasetStore` | Clase con estado | Configura raíz, opciones, reloj, gracia y lock local. |
| `ParquetWriteOptions` | Dataclass inmutable | Define compresión, diccionario, estadísticas y row groups. |
| `ParquetPart` | Dataclass inmutable | Asocia una `DatasetPartKey` con una tabla Arrow completa. |
| `ColumnFilter` | Dataclass inmutable | Declara un filtro que se traduce a Parquet/Arrow. |
| `FilterOperator` | `StrEnum` | Operadores `eq`, `ne`, `gt`, `ge`, `lt`, `le` e `in`. |
| `ParquetReadResult` | Dataclass inmutable | Tabla leída, targets, métricas, tokens y warnings. |
| `ParquetCleanupResult` | Dataclass inmutable | Conteos y bytes recuperados durante cleanup. |
| `ParquetDatasetError` | Excepción base | Raíz de errores propios del adapter. |
| `ParquetValidationError` | Excepción | Solicitud física inválida. |
| `ParquetLayoutError` | Excepción | Operación incompatible con el layout. |
| `ParquetSchemaError` | Excepción | Schema incompatible o ambiguo. |
| `ParquetPublicationNotFoundError` | Excepción | Publicación confirmada inexistente. |
| `ParquetReadError` | Excepción | Fallo al inspeccionar o leer. |
| `ParquetCorruptionError` | Excepción | Publicación confirmada inconsistente. |
| `ParquetWriteError` | Excepción | Escritura o commit físico fallido. |
| `__version__` | Texto | Versión expuesta en runtime. |

`manifest.py` y los modelos que comienzan con `_` son internos y no deben importarse desde los
consumidores.

## 1. Construcción del store

```python
from pathlib import Path

from atlanticus.datasets.parquet import ParquetDatasetStore

store = ParquetDatasetStore(
    root=Path('/srv/atlanticus/ada/datasets'),
)
```

El store acepta una ruta no vacía como `str` o `Path`; no exige que sea absoluta. Sin embargo, para
ejecuciones locales reproducibles Atlanticus recomienda que `VOLUMEN_PATH` sea absoluto y que la
composición construya la raíz como:

```text
<VOLUMEN_PATH>/<APPLICATION>/datasets/
```

Los procesos actuales utilizan `RuntimeConfiguration.application_root / 'datasets'`. El adapter
solo recibe la raíz ya resuelta, por lo que no altera la separación entre aplicaciones ni cambia el
volumen utilizado por un despliegue en contenedor.

La política completa de rutas locales pertenece a
[Ejecución local](../../docs/local-execution.md).

### Opciones de escritura

Los valores predeterminados de `ParquetWriteOptions` son:

| Opción | Valor |
|---|---|
| `compression` | `zstd` |
| `compression_level` | `3` |
| `use_dictionary` | `true` |
| `write_statistics` | `true` |
| `row_group_size` | `131072` |

```python
from atlanticus.datasets.parquet import ParquetDatasetStore, ParquetWriteOptions

store = ParquetDatasetStore(
    root='/srv/atlanticus/ada/datasets',
    write_options=ParquetWriteOptions(row_group_size=262_144),
)
```

El modelo valida tipos y valores estructurales, pero PyArrow determina finalmente si el codec y el
nivel son compatibles. Una combinación no soportada puede fallar al realizar la primera escritura.

## 2. Resolución de rutas

`path_for()` valida el target contra su `DatasetDefinition` y concatena exclusivamente los
segmentos retornados por `resolve_route_segments()`:

```python
target_path = store.path_for(
    definition=definition,
    target=target,
)
```

El store no recibe rutas libres por operación, no reconstruye la identidad y no recorre carpetas
para descubrir targets. La raíz física pertenece a la composición; la ruta relativa pertenece al
contrato de Datasets.

## 3. Layout de artefacto único

`SingleArtifactLayout` utiliza esta estructura:

```text
<root>/<ruta-relativa-del-target>/
└── data.parquet
```

### Reemplazo

```python
result = store.replace(
    definition=definition,
    target=target,
    table=arrow_table,
)
```

`replace()`:

1. valida definición, target, layout, tabla y schema;
2. retorna `skipped/empty_content` si la tabla no contiene filas;
3. lee la publicación vigente cuando existe;
4. retorna `unchanged` si tabla y metadata Arrow son idénticas;
5. escribe y fuerza a disco un temporal en el directorio del target;
6. reabre el Parquet y valida filas y schema;
7. confirma `data.parquet` mediante `os.replace()`;
8. fuerza a disco el directorio en sistemas distintos de Windows.

El vacío se detecta antes de crear la carpeta. Un fallo anterior al reemplazo conserva la
publicación previa.

### Merge

```python
result = store.merge(
    definition=definition,
    target=target,
    incoming=incremental_table,
    key_columns=('timestamp',),
    order_by=('timestamp',),
)
```

El merge aplica estas reglas:

- solo funciona con `SingleArtifactLayout`;
- exige al menos una columna de clave;
- las claves deben existir y no contener nulos;
- el schema entrante es la autoridad;
- una columna nueva se completa con nulos en las filas históricas;
- una columna retirada del schema entrante desaparece del resultado;
- una columna conservada no admite cambio de tipo silencioso;
- la última fila entrante para una clave reemplaza la fila completa, incluidos nulos;
- `order_by` ordena ascendentemente el resultado final;
- una entrada vacía produce `skipped/empty_content` sin modificar la publicación.

La operación lee, alinea, concatena y deduplica la publicación completa en memoria. No es un merge
out-of-core ni una actualización por row group; su tamaño máximo práctico depende de la memoria del
proceso.

## 4. Layout de conjunto de partes

`FileSetLayout` publica partes inmutables planas y confirma su composición mediante `current.json`:

```text
<root>/<ruta-relativa-del-target>/
├── current.json
├── shift_id=26199001--<sha256>.parquet
└── shift_id=26199002--<sha256>.parquet
```

No existe un subdirectorio `parts/`. El nombre físico combina dimensión, valor y hash SHA-256. El
manifest es la única autoridad: un archivo Parquet no referenciado permanece invisible.

### Publicación incremental

```python
from atlanticus.datasets.parquet import ParquetPart

result = store.publish_parts(
    definition=definition,
    target=target,
    incoming_parts=(
        ParquetPart(key=part_key, table=part_table),
    ),
)
```

Cada `ParquetPart` contiene el contenido completo de su identidad. Las partes vigentes no
mencionadas se conservan. Una parte solo se retira de forma explícita:

```python
result = store.publish_parts(
    definition=definition,
    target=target,
    remove_parts=(obsolete_part_key,),
)
```

Reglas relevantes:

- una parte no puede entrar y retirarse en la misma operación;
- todas las partes entrantes deben utilizar exactamente el mismo schema;
- si cualquier parte entrante está vacía, se omite la publicación completa;
- un target nuevo exige al menos una parte entrante;
- el conjunto confirmado nunca puede quedar sin partes;
- las partes preservadas pueden omitir columnas nuevas o conservar columnas retiradas;
- los tipos de las columnas que aún coinciden deben ser compatibles;
- una publicación con la misma firma lógica retorna `unchanged`;
- `current.json` se reemplaza únicamente después de validar todas las partes del siguiente conjunto.

Si falla el commit del manifest, el manifest anterior continúa vigente. Las partes nuevas que no
alcanzaron a ser referenciadas se consideran huérfanas y podrán limpiarse después de la gracia.

### Contrato del manifest

El manifest interno utiliza `format_version=1` e incluye:

- token de publicación;
- identificador exacto del target;
- fecha de commit en UTC;
- dimensión de parte;
- conteo total de filas;
- firma de publicación;
- schema Arrow serializado y su firma;
- identidad, ruta, filas, bytes y firma SHA-256 de cada parte.

Al leerlo se rechazan campos requeridos inválidos, schemas inconsistentes, partes duplicadas,
rutas que no coincidan con su identidad y firmas que no correspondan al contenido declarado.

## 5. Lectura

### Schema

```python
schema = store.read_schema(
    definition=definition,
    target=target,
)
```

La operación no materializa filas. En un artefacto único abre el footer Parquet. En un file set
lee el manifest y también inspecciona las partes confirmadas, incluyendo tamaño, firma y schema.
Por ello evita cargar row groups, pero no debe considerarse una consulta de costo constante.

### Target completo

```python
result = store.read(
    definition=definition,
    target=target,
)

table = result.table
```

Una publicación ausente genera `ParquetPublicationNotFoundError`; no se representa mediante una
tabla vacía.

### Proyección y filtros

```python
from atlanticus.datasets.parquet import ColumnFilter, FilterOperator

result = store.scan(
    definition=definition,
    targets=(day_1, day_2),
    columns=('timestamp', 'tonnage'),
    filters=(
        ColumnFilter('timestamp', FilterOperator.GREATER_THAN_OR_EQUAL, start_utc),
        ColumnFilter('timestamp', FilterOperator.LESS_THAN, end_utc),
    ),
)
```

Los filtros se combinan mediante `AND`. Se admiten:

| Operador | Valor |
|---|---|
| `FilterOperator.EQUAL` | `eq` |
| `FilterOperator.NOT_EQUAL` | `ne` |
| `FilterOperator.GREATER_THAN` | `gt` |
| `FilterOperator.GREATER_THAN_OR_EQUAL` | `ge` |
| `FilterOperator.LESS_THAN` | `lt` |
| `FilterOperator.LESS_THAN_OR_EQUAL` | `le` |
| `FilterOperator.IN` | `in` |

`IN` exige un iterable no vacío que no sea string. El resto de los operadores no acepta `None`.
Los valores de filtros sobre columnas físicas se convierten al tipo Arrow antes de ejecutar el
scan. Los filtros `eq` e `in` utilizados para seleccionar partes comparan la dimensión lógica como
texto.

Para varios targets, `columns` es obligatorio. La selección de targets siempre es explícita; el
store no descubre particiones históricas.

En `FileSetLayout`, un filtro `eq` o `in` sobre `part_dimension` descarta partes desde el manifest
antes de calcular sus firmas o abrirlas. Los demás filtros se envían al lector Parquet.

## 6. Evolución de schema

Al proyectar varias publicaciones:

- una columna solicitada ausente en una publicación se completa con nulos;
- el resultado registra un warning por la ausencia;
- una columna presente con tipos incompatibles provoca `ParquetSchemaError`;
- la metadata y nulabilidad de la versión autoritativa se conservan cuando es posible.

En un file set, el schema del manifest es el schema lógico vigente. Las partes antiguas pueden
carecer de columnas nuevas o contener columnas retiradas; el lector las alinea. Un cambio de tipo
en una columna aún vigente exige republicar o retirar las partes incompatibles.

No existe migración automática de tipos.

## 7. Resultado de lectura

`ParquetReadResult` entrega:

| Campo o propiedad | Significado |
|---|---|
| `table` | Tabla PyArrow materializada. |
| `targets` / `target_count` | Targets explícitos incluidos. |
| `artifact_count` | Archivos realmente leídos después del pruning. |
| `size_bytes` | Tamaño físico acumulado de esos artefactos. |
| `publication_tokens` | Tokens de manifests de file sets. |
| `warnings` | Columnas ausentes proyectadas como nulos. |
| `row_count` | Filas finales después de filtros. |

Targets, tokens y warnings se normalizan a tuplas inmutables y no admiten duplicados o strings
vacíos según corresponda.

## 8. Integridad y corrupción

Para un file set, cada lectura valida:

- estructura y versión del manifest;
- identidad del target y dimensión de parte;
- schema serializado y firma del schema;
- firma global de publicación;
- nombre, tamaño, filas y SHA-256 de cada parte seleccionada;
- compatibilidad entre schema físico y lógico.

Una parte ausente, alterada o incompatible falla la lectura completa con
`ParquetCorruptionError`; no se retornan resultados parciales.

Un artefacto único no posee manifest persistente. Su lectura comprueba que el archivo pueda abrirse,
que no esté vacío y que su schema sea válido, pero no dispone de una firma confirmada independiente
para detectar una sustitución por otro Parquet estructuralmente válido.

## 9. Limpieza

```python
result = store.cleanup(
    definition=definition,
    target=target,
)
```

La gracia predeterminada es de diez minutos. `cleanup()` elimina solamente:

- temporales que coinciden con el patrón interno del store;
- partes con nombre propio del layout que no estén referenciadas por el manifest vigente;
- candidatos cuya antigüedad basada en `mtime` supera la gracia.

No elimina `data.parquet`, `current.json`, partes confirmadas ni archivos manuales que no coincidan
con sus patrones. Cada escritura intenta la misma limpieza antes de comenzar.

`ParquetCleanupResult` informa temporales, partes huérfanas y bytes recuperados. La gracia debe ser
mayor que el máximo tiempo razonable de una escritura para no retirar un artefacto que aún pueda ser
observado por una operación concurrente.

## 10. Concurrencia y atomicidad

El store utiliza un `threading.RLock` por instancia:

- `merge()` protege el ciclo completo de lectura, combinación y reemplazo;
- `publish_parts()` protege lectura del manifest, escritura de partes y commit;
- `replace()` serializa el reemplazo físico, pero realiza su lectura y comparación inicial antes de
  entrar a ese lock;
- las escrituras físicas usan temporales en el mismo directorio y `os.replace()`;
- no existe transacción entre targets distintos.

El lock no coordina dos instancias del store, dos procesos, dos contenedores o dos nodos. La
composición debe garantizar un único escritor activo por target mediante el runtime o una política
externa.

La atomicidad presupone que temporal y destino están en el mismo filesystem. Las garantías reales
de `os.replace()`, `fsync()` y `mtime` deben validarse en el volumen donde se desplegará.

## Jerarquía de errores

| Error | También hereda de | Uso |
|---|---|---|
| `ParquetDatasetError` | `DatasetError` | Captura general del adapter. |
| `ParquetValidationError` | `DatasetValidationError` | Argumento, tabla, filtro o solicitud inválida. |
| `ParquetLayoutError` | `ParquetValidationError` | Operación incompatible con el layout. |
| `ParquetSchemaError` | `ParquetValidationError` | Schema o tipo incompatible. |
| `ParquetPublicationNotFoundError` | `FileNotFoundError` | Publicación todavía inexistente. |
| `ParquetReadError` | `ParquetDatasetError` | Lectura física fallida. |
| `ParquetCorruptionError` | `ParquetReadError` | Publicación confirmada inconsistente. |
| `ParquetWriteError` | `ParquetDatasetError` | Escritura o reemplazo fallido. |

Los mensajes técnicos permanecen en inglés.

## Dependencias y consumidores

El wheel tiene dos dependencias productivas directas:

- `atlanticus-datasets==0.1.0` para identidades, layouts, targets y resultados;
- `pyarrow==25.0.0` para tablas, schemas, compute y Parquet.

Sus ocho consumidores productivos directos declarados en el snapshot son:

| Capa | Consumidores |
|---|---|
| Backend | `atlanticus-datasets-runtime` |
| Procesos ADA | `kpis`, `kpis-historian` |
| Data Producers | `fabrica`, `notpii`, `pi`, `remanentes`, `sql` |

Todo consumidor que importe `atlanticus.datasets.parquet` debe declarar esta dependencia de forma
directa, aunque también instale Datasets Runtime.

## Source, diseño y mirror pedagógico

```text
backend/datasets-parquet/
├── pyproject.toml
├── src/atlanticus/datasets/parquet/
│   ├── __init__.py
│   ├── errors.py
│   ├── manifest.py
│   ├── models.py
│   ├── store.py
│   └── py.typed
├── commented/atlanticus/datasets/parquet/
├── docs/design.md
└── tests/
```

El código productivo vive en `src/`. `commented/` contiene el espejo pedagógico en español y queda
fuera del wheel. La prueba de paridad exige los mismos archivos Python y los mismos tokens no
comentarios.

[`docs/design.md`](docs/design.md) profundiza las fronteras, unidades de atomicidad, schema lógico,
consistencia de lectura y recuperación.

## Pruebas y validación

El snapshot contiene 32 funciones de prueba sin parametrización adicional:

| Archivo | Casos | Contrato validado |
|---|---:|---|
| `test_replace_and_scan.py` | 12 | Replace, lectura, proyección, filtros, schemas y fallos atómicos. |
| `test_poc_dispatch_operational_day.py` | 12 | Partes, manifest, pruning, evolución e integridad. |
| `test_poc_pi_wide_merge.py` | 3 | Merge wide, idempotencia y concurrencia local. |
| `test_cleanup.py` | 1 | Gracia y ownership de artefactos. |
| `test_routes.py` | 1 | Rutas declarativas separadas de la identidad. |
| `test_models.py` | 1 | Inmutabilidad de resultados. |
| `test_datasets_parquet_commented_mirror.py` | 2 | Paridad source/mirror. |

El gate oficial es:

```bash
cd backend
./scripts/validation/check.sh datasets-parquet --clean
```

El gate:

1. exige Python `3.14.2` ya instalado;
2. verifica el lock UV;
3. sincroniza sin instalación editable;
4. aplica Ruff fixes y formato al módulo y al mirror;
5. vuelve a comprobar Ruff y formato;
6. ejecuta los 32 tests;
7. valida `import atlanticus.datasets.parquet`;
8. construye exactamente un wheel en `backend/dist/`.

El validador no es pasivo: puede modificar el formato antes de ejecutar las comprobaciones.

| Necesidad | Documento propietario |
|---|---|
| Preparar Python, UV y el workspace | [Primeros pasos y desarrollo](../../docs/development.md) |
| Ejecutar y validar módulos localmente | [Ejecución local](../../docs/local-execution.md) |
| Construir e inspeccionar el wheel | [Empaquetado](../../docs/packaging.md) |
| Actualizar versión y consumidores | [Versionamiento](../../docs/versioning.md) |

## Construcción y contenido del wheel

El resultado sigue el patrón:

```text
backend/dist/atlanticus_datasets_parquet-<version>-py3-none-any.whl
```

El wheel debe incluir `atlanticus/datasets/parquet/*.py` y `py.typed`. No debe instalar `tests/`,
`commented/`, `docs/` ni caches como packages ejecutables.

El `pyproject.toml` actual declara `readme = "README.md"`. Setuptools incorpora el contenido como
descripción de metadata, pero no convierte sus enlaces relativos ni incorpora automáticamente los
documentos y assets externos. La instalación no se rompe, aunque la navegación de la descripción
puede quedar incompleta fuera del monorepo.

## Actualización de versión

Antes de publicar una revisión deben evaluarse como contrato público:

- operaciones, argumentos y resultados de `ParquetDatasetStore`;
- defaults de `ParquetWriteOptions`;
- layouts físicos, nombres de archivos y manifest versionado;
- semántica de replace, merge, partes, vacío e idempotencia;
- evolución de schema y reglas de proyección;
- filtros, pruning y errores públicos;
- atomicidad, limpieza, firmas y tolerancia a fallos;
- exports y `__version__`.

Una actualización debe mantener alineados:

1. `[project].version` en `pyproject.toml`;
2. `__version__` en source y mirror;
3. pin de `atlanticus-datasets` y compatibilidad con PyArrow;
4. pins exactos de los ocho consumidores directos;
5. `backend/uv.lock` y locks derivados;
6. pruebas, diseño y README;
7. wheels y artifacts regenerados.

Ruff, formato, pruebas y construcción del wheel se ejecutan antes y después del cambio conforme a
[Versionamiento](../../docs/versioning.md).

## Elementos no verificados o pendientes

- No se ejecutó el gate durante esta revisión documental porque el entorno disponible no posee
  Python `3.14.2` y no se permite descargarlo automáticamente.
- La declaración `readme = "README.md"` continúa pendiente de una política transversal para todos
  los wheels.
- El lock protege únicamente una instancia en un proceso; no existe coordinación multiproceso o
  distribuida dentro del adapter.
- Dos `replace()` concurrentes pueden comparar fuera del lock y terminar con semántica de último
  commit; no existe una prueba que defina ese caso como contrato intencional.
- `merge()` materializa la publicación completa y no tiene un límite de tamaño documentado o
  aplicado.
- `read_schema()` sobre file sets calcula firmas de las partes y puede ser costoso para conjuntos
  grandes.
- Un artefacto único no mantiene manifest ni firma confirmada para validar su contenido contra una
  referencia persistente.
- `ParquetWriteOptions` no comprueba anticipadamente que codec y nivel sean compatibles con
  PyArrow; el error aparece durante la escritura.
- No se verificaron las garantías de replace, fsync, mtime y permisos sobre WSL, bind mounts, NFS,
  SMB u otros volúmenes de despliegue.
- No existe retención ni rollback de publicaciones anteriores; los huérfanos elegibles se eliminan
  después de la gracia.
- El parser JSON del manifest no detecta nombres de campo duplicados; `json.loads()` conserva el
  último valor antes de ejecutar las validaciones estructurales.

## Control documental

La versión `1.0.0` corresponde únicamente a este README. La versión técnica del wheel continúa
siendo la declarada por su `pyproject.toml`.

El documento permanece **En revisión**. Su aprobación no publica una versión nueva ni resuelve
automáticamente los riesgos técnicos registrados.

---

[Volver a Backend](../README.md) · [Volver a Datasets](../datasets/README.md) ·
[Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)
