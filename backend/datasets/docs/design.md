# Diseño de `atlanticus-datasets`

## Responsabilidad

Datasets responde dos preguntas neutrales: “¿qué unidad lógica intenta publicar el job?” y “¿qué
ocurrió con esa unidad?”. No decide cómo obtener, transformar, serializar o consumir los datos.

| Información | Dueño |
|---|---|
| Dataset, materialización, partición, parte, target y ruta relativa declarativa | `atlanticus-datasets` |
| Raíz física, archivos, temporales, manifiestos y atomicidad | Adaptador de formato |
| Columnas, tipos, activación y destinos | Catálogo de la aplicación |
| Descarga, transformación y watermark | Job de ingesta |
| Unión de requerimientos y memoria de ejecución | Futuro `DatasetContext` |
| Resumen confirmado | `atlanticus-state` a solicitud del job |
| Detalle de faltantes y reintentos | Pipeline control |

## Identidad lógica

`DatasetKey` contiene namespace y nombre, pero no aplicación. La aplicación aparece al resolver un
`DatasetTarget`, porque una misma definición puede reutilizarse en distintas composiciones.

```text
DatasetKey:    ingestion/dispatch/truck-events
Materialidad: operational-week
Partición:    operational_year=2026, operational_week=W30
Target:       application=ada + todos los elementos anteriores
```

Las identidades nunca se normalizan silenciosamente. Nombres, dimensiones y valores deben llegar
como strings seguros y explícitos. Las particiones se reordenan según la definición para producir
una identidad estable independiente del orden del mapping de entrada.


## Rutas relativas

La identidad de un `DatasetTarget` y su ruta de almacenamiento son contratos distintos. La
identidad permanece estable para resultados, trazabilidad y manifiestos. `DatasetDefinition` puede
declarar `route_segments` para su ubicación relativa y cada `MaterializationDefinition` puede
declarar sus propios segmentos. `None` deriva la ruta desde la identidad; una tupla vacía en la
materialización omite ese nivel cuando la partición ya expresa la representación.

El adaptador aporta exclusivamente la raíz física y agrega nombres de artefactos. Por ejemplo, con
raíz `application=ada/datasets`, ruta de dataset `dispatch/std_shift_dumps` y materialización sin
segmento, una partición produce:

```text
dispatch/std_shift_dumps/year=2026/month=08/day=06/turn=001/data.parquet
```

Las rutas se validan como segmentos seguros. No aceptan rutas absolutas, separadores embebidos ni
`..`. Dos materializaciones de una misma definición tampoco pueden resolver la misma ruta relativa.

## Materializaciones y layouts

La materialización expresa una representación con política propia de columnas definida fuera del
wheel. No implica retención ni agregación por su nombre.

`SingleArtifactLayout` cubre snapshots y particiones con un único artefacto. `FileSetLayout` cubre
un conjunto de partes con identidad semántica, por ejemplo un turno por `shift_id`. El nombre
físico de cada parte puede ser opaco; su relación con el `shift_id` pertenece al manifiesto del
adaptador.

Una dimensión de parte no puede duplicar una dimensión de partición. La primera distingue
fragmentos dentro de un target; la segunda distingue targets históricos independientes.

## Unidad de atomicidad

Cada target es una unidad independiente. En un layout de archivo único, el adaptador confirmará el
artefacto completo. En un file set, confirmará el conjunto completo mediante un mecanismo como un
manifiesto atómico. `atlanticus-datasets` no prescribe esa representación.

Un lote puede tener resultados parciales. Las unidades correctas permanecen confirmadas y los
fallos se resumen por separado. No existe rollback global entre targets o particiones.

## Invariante de vacíos

Un contenido vacío rompe el contrato de publicación porque no puede distinguirse con seguridad de
una fuente incompleta, un filtro incorrecto o una consulta transitoria. Por eso no existe una
opción para publicarlo.

El guard se ejecuta antes de I/O:

```text
calcular item_count
        │
        ├── 0  → skipped/empty_content → conservar publicación y state
        │
        └── >0 → continuar con el adaptador físico
```

Un resultado `skipped` tiene `artifact_count=0`, no contiene `size_bytes` ni firma y siempre marca
calidad `warning`. Un resultado `committed` o `unchanged` exige al menos un item y un artefacto.
Cuando exista `datasets-parquet`, sus operaciones `replace`, `merge` y `publish_parts` deberán
evaluar el vacío antes de crear la carpeta de una partición, un temporal o un manifiesto.

## Calidad y estado técnico

`PublicationStatus` describe el efecto técnico: commit, sin cambios o skip. `PublicationQuality`
describe si el contenido utilizable presenta warnings. Mantener ambas dimensiones evita confundir
atomicidad con completitud.

Los errores inesperados continúan siendo excepciones en la operación individual. El coordinador de
un lote puede reducirlas a `DatasetPublicationFailure` para conservar el resultado de otros
targets. El mensaje compacto no reemplaza el evento ni el registro detallado de pipeline control.

## Concurrencia

La versión inicial presupone un solo escritor activo por target o partición. El runtime coordina el
servicio; este wheel no implementa locks, leases ni compare-and-swap. Los adaptadores serán
responsables del commit atómico dentro de ese supuesto.

## Evolución prevista

`atlanticus-datasets-parquet` dependerá de este contrato e incorporará `replace`, `merge`,
publicación de partes, manifiestos, recuperación de temporales y lectura proyectada. Los catálogos
de PI y Dispatch seguirán fuera de ambos wheels. Una nueva materialización o dimensión en ADA no
debe exigir una versión nueva del core.
