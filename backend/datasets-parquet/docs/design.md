<p align="right">
  <img src="../../../docs/assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Diseño de `atlanticus-datasets-parquet`

[Volver a Datasets Parquet](../README.md) · [Volver a Backend](../../README.md) ·
[Volver al índice de documentación](../../../docs/README.md)

| Referencia | Valor |
|---|---|
| Versión del documento | `1.0.0` |
| Estado | En revisión |
| Documento propietario | `backend/datasets-parquet/README.md` |
| Versión técnica analizada | `0.2.0` |
| Tipo | Apéndice arquitectónico |

## Frontera

Este wheel adapta los contratos neutrales de `atlanticus-datasets` a archivos Parquet locales. Su
frontera tabular es `pyarrow.Table`. La normalización de nombres, tipos de negocio, timestamps,
sentinelas y pivots se completa antes de invocarlo.

La composición elige la raíz física y los targets explícitos. El adapter decide cómo escribir,
confirmar, inspeccionar y leer los archivos bajo esa raíz.

## Resolución de rutas

`ParquetDatasetStore` recibe una raíz física y solicita a `DatasetDefinition` los segmentos
relativos validados del target. No vuelve a derivar rutas desde la identidad ni fija el prefijo
`datasets`.

Los procesos actuales construyen la raíz como
`RuntimeConfiguration.application_root / 'datasets'`. De ese modo, la separación entre
`VOLUMEN_PATH`, aplicación y dataset permanece fuera del wheel.

El store no descubre targets mediante recorridos del filesystem. Quien conoce el scope debe
resolverlos antes de leer.

## Unidades de atomicidad

`SingleArtifactLayout` confirma `data.parquet` mediante un temporal en el mismo directorio.
`FileSetLayout` escribe partes inmutables direccionadas por contenido y confirma su composición con
un único reemplazo de `current.json`.

No existe una transacción entre targets. Dos días o materializaciones son publicaciones
independientes aunque un lector los solicite en el mismo `scan()`.

El store serializa operaciones críticas con un lock reentrante por instancia. `merge()` y
`publish_parts()` protegen su ciclo completo; `replace()` realiza la comparación inicial antes de
entrar al lock de escritura. La composición debe mantener un único escritor activo por target; dos
procesos o contenedores no comparten ese lock.

## Durabilidad local

Los temporales se escriben en el mismo directorio del destino, se fuerzan a disco, se reabren y se
validan antes del reemplazo. Después del `os.replace()`, el store intenta `fsync()` del directorio
en sistemas distintos de Windows.

El adapter no ejecuta `chmod`. La identidad del proceso, el propietario del volumen y el `umask`
pertenecen al desarrollo local o al despliegue.

Estas garantías presuponen un filesystem con semántica compatible. Deben verificarse sobre el
volumen real utilizado por WSL, Docker o la plataforma desplegada.

## Artefacto único

En `SingleArtifactLayout`, el Parquet vigente es la autoridad. `replace()` compara la tabla actual
con la entrante y evita el reemplazo si ambas son idénticas. `merge()` utiliza el schema entrante
como autoridad, carga el contenido vigente, alinea columnas, concatena y conserva la última fila
por clave.

El merge completo en memoria simplifica el contrato y mantiene determinismo, pero no es adecuado
sin evaluación para datasets mayores que la memoria disponible.

El artefacto único no conserva un manifest separado. Puede detectar un archivo ilegible, vacío o
con schema inválido, pero no contrastar un Parquet válido contra una firma confirmada previamente.

## Conjunto de partes

En `FileSetLayout`, cada parte se nombra con su identidad y SHA-256. `current.json` contiene el
schema lógico, firmas, conteos, tamaños y lista exacta de partes confirmadas.

Una actualización escribe primero las nuevas partes y confirma el conjunto al reemplazar el
manifest. Si falla ese reemplazo, el conjunto anterior continúa vigente y las partes nuevas quedan
como huérfanas invisibles.

Una parte entrante representa su contenido completo. Las no mencionadas se conservan; las que deben
desaparecer se entregan explícitamente mediante `remove_parts`. El conjunto confirmado nunca puede
quedar vacío.

## Schema lógico

En un artefacto único, el schema del Parquet vigente es la autoridad. En un file set, el manifest
almacena el schema Arrow serializado, su firma y una representación legible de sus campos.

Una parte antigua puede carecer de una columna nueva o conservar una retirada. El lector alinea
contra el schema lógico: agrega nulos para la primera y omite la segunda. Un cambio de tipo en una
columna todavía presente es incompatible y exige republicar o retirar las partes afectadas.

`read_schema()` no materializa filas. Para un artefacto único inspecciona el footer. Para un file
set, la implementación actual también valida tamaños, firmas y schemas de las partes confirmadas;
por tanto, puede leer todos sus bytes para calcular SHA-256 y no debe asumirse como una operación de
metadata barata.

## Consistencia de lectura

El lector abre exclusivamente `data.parquet` o las rutas indicadas por el manifest. Una parte
referenciada inexistente, vacía o con tamaño, filas, firma o schema incompatibles falla toda la
lectura. No se retornan resultados parciales.

Los filtros de columnas se aplican mediante Parquet. Para `eq` e `in` sobre `part_dimension`, el
manifest permite descartar archivos antes de inspeccionarlos. La selección de targets históricos
sigue siendo responsabilidad de la capa que entiende el scope.

## Vacíos

El guard se ejecuta antes de crear o reemplazar una publicación:

```text
calcular filas
      │
      ├── 0  → skipped/empty_content → conservar publicación vigente
      │
      └── >0 → escribir y validar → commit atómico
```

En un file set, una sola parte entrante vacía omite la operación completa, incluidas otras partes y
remociones entregadas en esa llamada.

## Recuperación y limpieza

Un fallo previo al commit deja un temporal o una parte no referenciada. La publicación anterior
permanece vigente. La siguiente ejecución puede publicar nuevamente de forma idempotente; el
huérfano no participa en las lecturas.

La gracia de limpieza es diez minutos por defecto. Solo se eliminan patrones propios del store y
artefactos no referenciados cuyo `mtime` supera esa gracia. La configuración debe exceder el máximo
periodo razonable de una escritura.

No existe versionado histórico ni rollback. Las copias antiguas no referenciadas pasan a ser
huérfanos elegibles para limpieza.

## Control documental

La versión `1.0.0` corresponde únicamente a este apéndice. El documento permanece **En revisión**
y se valida junto con `backend/datasets-parquet/README.md`.

---

[Volver a Datasets Parquet](../README.md) · [Volver a Backend](../../README.md) ·
[Volver al índice de documentación](../../../docs/README.md)
