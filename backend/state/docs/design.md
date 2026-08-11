# Diseño de `atlanticus-state`

## Responsabilidad

State responde una pregunta pequeña: “¿cuál fue el último hecho técnico confirmado que este job
necesita recordar para decidir su siguiente ejecución?”. Es un store genérico y no un modelo
central de todas las fuentes.

| Información | Dueño |
|---|---|
| Watermark agregado, change token y calidad resumida | `atlanticus-state` a solicitud del job |
| Tags PI, columnas Dispatch, archivos, particiones y manifest | Ingestion y futuros datasets |
| Detalle de tags/columnas/horas faltantes | Pipeline control |
| Eventos, errores, tiempos y cantidades | Observability |
| Lease y presupuesto de ejecución | Job Runtime |

Agregar una fuente o package no modifica state. El nuevo consumidor crea un `StateKey` bajo su
propio namespace y define su payload JSON compacto.

## Persistencia

La ruta física es:

```text
${VOLUMEN_PATH}/${APPLICATION}/.runtime/state/<namespace...>/<name>.json
```

La aplicación es la primera frontera bajo el volumen. `state` está separado físicamente de
`.runtime/leases`, por lo que una clave de estado no puede sobrescribir una lease. Los segmentos se
validan y nunca se normalizan silenciosamente, para evitar colisiones entre identidades. El
documento usa JSON canónico, un schema explícito y un límite configurable de bytes.

La escritura ocurre en el mismo directorio del destino:

1. crea un temporal exclusivo;
2. escribe el documento completo;
3. ejecuta `flush` y `fsync`;
4. reemplaza el destino mediante `os.replace`;
5. elimina cualquier temporal remanente.

Un `SIGKILL` puede impedir el paso 5. Antes de crear el siguiente temporal para una clave, el store
elimina sólo archivos que coinciden exactamente con su nombre final y un token UUID hexadecimal de
32 caracteres. No recorre otros namespaces ni borra archivos temporales desconocidos. Si la
limpieza falla, no intenta confirmar el nuevo documento y conserva el último valor estable.

El contrato inicial presupone un escritor por `StateKey`, garantizado por el job y la lease del
servicio. El lock interno sólo ordena hilos del mismo store; no pretende ser un lock distribuido.
Si aparece un consumidor con escritores simultáneos reales, deberá incorporarse compare-and-swap
como una evolución explícita y no como complejidad preventiva.

## Publicación parcial

Una publicación puede ser atómica y contener calidad degradada. Faltas de columnas, tags, horas o
datos individuales generan warning, no invalidan todo el artefacto cuando el resto sigue siendo
utilizable. El job publica un nuevo change token y un resumen compacto de calidad; los consumidores
posteriores continúan y marcan como inválidos sólo los resultados afectados.

No se publica un nuevo estado cuando no puede escribirse o leerse el artefacto, está corrupto o
inutilizable, la ejecución se cancela antes del commit, o no puede confirmarse el propio state. El
último documento confirmado sigue siendo la referencia y pipeline control registra el intento.

## Crecimiento acotado

El store principal reemplaza su documento y por diseño no crece con cada ejecución. Tampoco guarda
las 700–1000 keys internas de una ingesta PI: esos checkpoints detallados requerirán una estructura
columnar atómica dentro del dominio de ingestion/datasets.

`ExpiringKeySet` es la excepción diseñada para deduplicación transitoria. Aun así permanece acotado
por TTL, capacidad máxima, hashes sin texto claro y purga automática. Sus parámetros son parte del
job porque dependen de la ventana de reentrega de la fuente.

## Fallos y telemetría

Un estado ausente se expresa como `None`. Un documento existente pero inválido nunca se interpreta
como ausente: genera `StateCorruptionError` o `StateSchemaError`. La lectura está acotada al máximo
configurado más un byte y rechaza UTF-8 inválido, claves duplicadas, profundidad excesiva y números
no finitos. Los fallos de I/O, tamaño y validación también se propagan mediante errores públicos.

Los eventos correctos son locales. Las condiciones anormales usan audiencia operacional, de modo
que la extensión Azure pueda exportarlas en `slim`. La telemetría contiene identidad lógica,
duración, bytes y cantidades; no contiene payloads, watermarks ni message IDs. Si el logger falla,
la operación de estado conserva su resultado original y una escritura confirmada continúa siendo
exitosa.
