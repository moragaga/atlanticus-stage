# Diseño de atlanticus-datasets-parquet 0.2.0

## Frontera

Este wheel adapta los contratos neutrales de `atlanticus-datasets` a archivos locales Parquet. Su
frontera tabular es Arrow. La normalización de nombres, tipos de negocio, timestamps, sentinelas y
pivots se completa antes de invocarlo.


## Resolución de rutas

`ParquetDatasetStore` recibe una raíz física y solicita a `DatasetDefinition` los segmentos
relativos validados del target. El store no vuelve a derivar rutas desde la identidad ni fija el
prefijo `datasets`. La composición decide la raíz, mientras el contrato del dataset decide la ruta
relativa. Así diferentes procesos reutilizan el mismo store sin crear adaptadores de almacenamiento
por dominio.

## Unidades de atomicidad

`SingleArtifactLayout` confirma `data.parquet` mediante un temporal en el mismo filesystem.
`FileSetLayout` escribe partes inmutables direccionadas por contenido y confirma su composición con
un único reemplazo de `current.json`.

El adaptador no modifica permisos mediante `chmod`. La identidad del proceso, el propietario del
bind mount y el `umask` se resuelven fuera del wheel, en el despliegue o en la batería runtime local.

No existe una transacción entre targets. Por ejemplo, dos días granulares son publicaciones
independientes aunque un lector los solicite en un mismo `scan()`.

El store serializa las escrituras concurrentes dentro de una misma instancia. La composición debe
mantener un único proceso escritor por target; la coordinación distribuida entre procesos queda
fuera de este adaptador.

## Schema lógico

En un artefacto único, el schema del Parquet vigente es la autoridad. En un file set, el manifiesto
almacena el schema Arrow serializado, su firma y una representación legible de sus campos.

`read_schema()` resuelve la publicación confirmada y devuelve únicamente su schema Arrow. No ejecuta
un `scan()` ni carga filas; permite a capas superiores validar/evolucionar columnas sin pagar I/O de
datos cuando solo necesitan metadatos.

Una parte física antigua puede carecer de una columna nueva o conservar una columna retirada. El
lector alinea contra el schema lógico: agrega nulos para la primera y omite la segunda. Un cambio de
tipo en una columna todavía presente es incompatible y exige republicar o retirar las partes
afectadas.

## Consistencia de lectura

El lector abre exclusivamente `data.parquet` o las rutas indicadas por el manifiesto que leyó. Una
parte referenciada inexistente, vacía o con tamaño, filas o firma física diferentes falla toda la
lectura. No se retornan resultados parciales.

Los filtros se aplican durante la lectura Parquet. Para `eq` e `in` sobre `part_dimension`, el
manifiesto permite descartar archivos antes de abrirlos. La selección de targets históricos sigue
siendo responsabilidad de la capa que entiende el scope.

## Recuperación

Un fallo previo al commit deja un temporal o una parte no referenciada. La publicación anterior
permanece vigente. La siguiente ejecución vuelve a obtener datos desde su estado de pipeline y
publica idempotentemente; el archivo huérfano no participa.

La gracia de limpieza es diez minutos por defecto. La composición debe configurarla por encima del
máximo periodo posible de escritura. Esto protege a un lector que alcanzó a leer el manifiesto
anterior antes del commit nuevo.
