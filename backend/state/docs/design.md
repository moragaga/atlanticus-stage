# Diseño de `atlanticus-state`

## Responsabilidad

State responde una pregunta pequeña: “¿cuál fue el último hecho técnico confirmado que este job
necesita recordar para decidir su siguiente ejecución?”. También ofrece el primitive físico mínimo
para reemplazar objetos JSON de forma atómica cuando un dominio necesita controlar su propio schema
y layout. No es un modelo central de todas las fuentes ni un journal genérico.

| Información | Dueño |
|---|---|
| Watermark agregado, change token y calidad resumida | `AtomicStateStore` a solicitud del job |
| Reemplazo físico de un JSON con schema/layout externo | `AtomicJsonStore` |
| WAL, offsets, replay y frontera durable | Dominio que implementa el journal |
| Tags PI, columnas Dispatch, archivos, particiones y manifest | Ingestion y datasets |
| Detalle de tags/columnas/horas faltantes | Pipeline control |
| Eventos, errores, tiempos y cantidades | Observability |
| Lease y presupuesto de ejecución | Job Runtime |

Agregar una fuente o package no modifica state. Un consumidor de `AtomicStateStore` crea un
`StateKey` bajo su propio namespace y define su payload JSON compacto. Un consumidor de
`AtomicJsonStore` define una raíz absoluta, una ruta `.json` relativa y el schema completo del
objeto persistido.

## Persistencia de `AtomicStateStore`

La ruta física es:

```text
${VOLUMEN_PATH}/${APPLICATION}/.runtime/state/<namespace...>/<name>.json
```

La aplicación es la primera frontera bajo el volumen. `state` está separado físicamente de
`.runtime/leases`, por lo que una clave de estado no puede sobrescribir una lease. Los segmentos se
validan y nunca se normalizan silenciosamente, para evitar colisiones entre identidades. El
documento usa JSON canónico, un schema explícito y un límite configurable de bytes.

## Persistencia de `AtomicJsonStore`

La ruta física queda deliberadamente bajo control del consumidor:

```text
${ROOT_PATH}/<relative-path>.json
```

`root_path` debe ser absoluto. La ruta entregada en cada operación debe ser relativa, terminar en
`.json` y no puede contener `.` o `..`. El primitive no añade un envelope ni conoce `application`,
`.runtime/state`, Alarm Engine, KPI u otra semántica de dominio.

Esta separación permite que un dominio conserve contratos propios como `GroupRuntimeSnapshot` o
`JournalHead` sin duplicar el mecanismo de `flush` + `fsync` + `os.replace` de Atlanticus. Un WAL
append-only no usa este primitive para el append; sólo puede reutilizarlo para documentos que se
publican mediante reemplazo completo.

## Commit atómico

El reemplazo ocurre en el mismo directorio del destino:

1. crea un temporal exclusivo;
2. escribe el documento completo;
3. ejecuta `flush` y `fsync` sobre el archivo;
4. reemplaza el destino mediante `os.replace`;
5. ejecuta `fsync` del directorio cuando la plataforma lo permite;
6. elimina cualquier temporal remanente en la salida normal.

Un `SIGKILL` puede impedir la limpieza final. Antes de crear el siguiente temporal para el mismo
destino, el primitive elimina sólo archivos que coinciden exactamente con el nombre final y un token
UUID hexadecimal de 32 caracteres. No recorre otros namespaces ni borra temporales desconocidos. Si
la limpieza falla, no intenta confirmar el nuevo documento y conserva el último valor estable.

El contrato presupone un escritor autorizado por destino. El lock interno ordena hilos del mismo
store; no pretende ser un lock distribuido ni realizar fencing. Cuando un dominio necesita excluir
stale writers, debe integrar explícitamente su autoridad antes del commit o mediante un primitive
físico que pueda demostrarse necesario.

## Política de tamaño

`DEFAULT_MAX_DOCUMENT_BYTES` continúa siendo 1 MiB para preservar un default defensivo. Ambos stores
aceptan:

```text
max_document_bytes = entero positivo  -> límite aplicativo explícito
max_document_bytes = None             -> sin límite aplicativo de Atlanticus
```

`None` no significa memoria o filesystem infinitos. Significa únicamente que esta biblioteca no
rechaza el documento por tamaño. La elección pertenece al consumidor y debe acompañarse de pruebas
de carga cuando se espere crecimiento significativo.

## Publicación parcial

Una publicación puede ser atómica y contener calidad degradada. Faltas de columnas, tags, horas o
datos individuales generan warning, no invalidan todo el artefacto cuando el resto sigue siendo
utilizable. El job publica un nuevo change token y un resumen compacto de calidad; los consumidores
posteriores continúan y marcan como inválidos sólo los resultados afectados.

No se publica un nuevo estado cuando no puede escribirse o leerse el artefacto, está corrupto o
inutilizable, la ejecución se cancela antes del commit, o no puede confirmarse el propio state. El
último documento confirmado sigue siendo la referencia y pipeline control registra el intento.

## Crecimiento acotado

`AtomicStateStore` reemplaza su documento y por diseño no crece con cada ejecución. Tampoco guarda
las 700–1000 keys internas de una ingesta PI: esos checkpoints detallados requieren una estructura
propia dentro del dominio correspondiente.

`ExpiringKeySet` es la excepción diseñada para deduplicación transitoria. Aun así permanece acotado
por TTL, capacidad máxima, hashes sin texto claro y purga automática. Sus parámetros son parte del
job porque dependen de la ventana de reentrega de la fuente.

`AtomicJsonStore(max_document_bytes=None)` permite documentos sin límite aplicativo cuando el dominio
lo requiere, pero no convierte state en almacenamiento histórico ni append-only.

## Fallos y telemetría

Un estado ausente se expresa como `None`. Un documento existente pero inválido nunca se interpreta
como ausente: genera `StateCorruptionError` o `StateSchemaError` cuando corresponde. La lectura con
límite está acotada al máximo configurado más un byte y rechaza UTF-8 inválido, claves duplicadas,
profundidad excesiva y números no finitos. Los fallos de I/O, tamaño y validación también se propagan
mediante errores públicos.

`AtomicStateStore` mantiene su telemetría existente: eventos correctos locales y condiciones
anormales con audiencia operacional para que la extensión Azure pueda exportarlas en `slim`. La
telemetría contiene identidad lógica, duración, bytes y cantidades; no contiene payloads, watermarks
ni message IDs. Si el logger falla, la operación conserva su resultado original.

`AtomicJsonStore` es deliberadamente un primitive físico y no emite eventos de dominio. El consumidor
que lo compone es responsable de registrar la semántica de la operación sin exponer payloads.
