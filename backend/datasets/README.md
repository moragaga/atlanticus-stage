# `atlanticus-datasets`

`atlanticus-datasets==0.1.0` define el contrato neutral que permite identificar datasets,
declarar sus materializaciones y describir el resultado de una publicación. No lee ni escribe
archivos y no conoce Pandas, PyArrow, Parquet, JSON, PI, Dispatch ni ADA.

Los adaptadores físicos dependen de este wheel:

```text
atlanticus-datasets-parquet
            ↓
   atlanticus-datasets
```

La dependencia inversa no está permitida.

## Conceptos

| Concepto | Responsabilidad | Ejemplo |
|---|---|---|
| Dataset | Conjunto lógico | `ingestion/dispatch/truck-events` |
| Materialización | Representación publicable | `operational-week` |
| Partición | Unidad histórica concreta | `operational_week=W30` |
| Parte | Fragmento lógico de un file set | `shift_id=26199001` |
| Target | Unidad independiente de commit | Aplicación + dataset + materialización + partición |

`latest` o `current` no son necesariamente particiones. Son materializaciones sin dimensiones
históricas cuando reemplazan un único target vigente.

## Definición

```python
from atlanticus.datasets import (
    DatasetDefinition,
    DatasetKey,
    FileSetLayout,
    MaterializationDefinition,
    SingleArtifactLayout,
)

dispatch = DatasetDefinition(
    key=DatasetKey(
        namespace=('ingestion', 'dispatch'),
        name='truck-events',
    ),
    materializations=(
        MaterializationDefinition(
            name='current',
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

Las columnas, tipos, activación y asignación a materializaciones pertenecen al catálogo de la
aplicación. `atlanticus-datasets` sólo valida identidades, dimensiones y compatibilidad lógica.

## Resolución de targets y partes

```python
target = dispatch.resolve_target(
    application='ada',
    materialization='operational-week',
    partition={
        'operational_week': 'W30',
        'operational_year': '2026',
    },
)

part = dispatch.resolve_part(
    target=target,
    value='26199001',
)
```

La definición impone el orden canónico de la partición, aunque el mapping llegue en otro orden:

```text
application=ada/
ingestion/
dispatch/
datasets/
truck-events/
operational-week/
operational_year=2026/
operational_week=W30
```

La dirección sigue siendo lógica. El futuro adaptador Parquet decidirá nombres como
`data.parquet`, `current.json` o `part-<token>.parquet`. La parte se identifica semánticamente como
`shift_id=26199001`, pero no obliga a incorporar el `shift_id` en el nombre del archivo.

## Layouts

`SingleArtifactLayout` representa un target mediante un artefacto confirmado. Puede ser un
snapshot sin partición o un archivo único por partición.

`FileSetLayout` representa un target mediante varias partes y exige una dimensión lógica para
identificarlas. El adaptador deberá confirmar el conjunto mediante su mecanismo atómico, por
ejemplo un manifiesto, sin que ese detalle ingrese al core.

## Resultados

Los estados individuales son:

- `committed`: se confirmó contenido nuevo;
- `unchanged`: la publicación confirmada ya contenía el mismo resultado;
- `skipped`: no se inició una escritura por contenido vacío.

Atomicidad y calidad son independientes. Un resultado `committed` puede tener calidad `warning`
cuando el contenido sigue siendo utilizable, pero faltan elementos no críticos.

```python
from datetime import UTC, datetime

from atlanticus.datasets import DatasetPublicationResult

empty = DatasetPublicationResult.skipped_empty(
    target=target,
    finished_at_utc=datetime.now(UTC),
)
```

## Regla de contenido vacío

El contenido vacío nunca reemplaza una publicación, nunca crea una partición y nunca debe iniciar
I/O. No existe `allow empty` ni una política configurable.

El contrato lo expresa de manera estricta:

- `committed` y `unchanged` exigen `item_count > 0` y `artifact_count > 0`;
- un vacío sólo puede representarse como `skipped` con motivo `empty_content`;
- el resultado vacío exige cero artefactos y prohíbe bytes o firma de escritura;
- el adaptador físico debe ejecutar este guard antes de crear directorios, temporales o manifiestos;
- la publicación confirmada y el state anterior permanecen intactos.

## Publicaciones múltiples

`DatasetBatchResult` agrega resultados y fallos por target sin simular una transacción global. Un
fallo no revierte targets ya confirmados:

```text
current             → committed
operational-day     → unchanged
operational-week    → failed
lote                → warning
```

Pipeline control conserva el detalle extenso. State recibe sólo el resumen que el job decida
confirmar después de verificar las publicaciones válidas.

## Fuera de alcance

Esta versión no incorpora:

- lectura o escritura física;
- Pandas, PyArrow o extensiones de archivo;
- `replace`, `merge`, `publish_parts` o `scan`;
- manifiestos concretos;
- selección de columnas o tipos;
- catálogos PI o Dispatch;
- `DatasetContext`;
- locks, leases, compactación, backfill o historia.

El diseño completo se encuentra en [`docs/design.md`](docs/design.md).

## Validación de integración

Además de las pruebas unitarias de este package, la batería de Runtime instala el wheel construido
y lo compone con `atlanticus-job-runtime` y `atlanticus-state` dentro de Docker. La prueba ejecuta
una iteración real, conserva sus resultados en la carpeta bind y los verifica desde un segundo
contenedor:

```bash
cd ../runtime/docker
bash 07_datasets_contract_test.sh
```

Este smoke valida el contrato neutral y su integración entre wheels. La escritura de Parquet,
manifiestos y partes físicas se probará en `atlanticus-datasets-parquet`, porque todavía no forma
parte de `atlanticus-datasets==0.1.0`.
