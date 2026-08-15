# Contrato operativo del process PI Web API

## Identidad y ownership

La identidad de un productor se interpreta dentro del namespace `ENVIRONMENT + APPLICATION`.
Dentro de ese namespace, cada `process_key` debe ser único y representa un productor lógico estable.
Cambiar el `process_key` crea un productor nuevo; no es un rename transparente ni hereda estado de forma implícita.

Una misma clave puede existir en otra `APPLICATION`, porque cambia el namespace físico completo.

Cada dataset materializado tiene exactamente un productor propietario. Dos processes distintos no pueden escribir sobre el mismo destino, aunque las escrituras individuales sean atómicas. La atomicidad protege contra archivos parciales; no resuelve coordinación multi-writer.

## Exclusión de ejecución

El process usa el lease provisto por `atlanticus.runtime` con este contrato:

- `job_key = pi-web-api-materialization`
- `lease_timeout_seconds = 30`
- `lease_renew_seconds = 10`
- `lease_wait_seconds = adaptive`
- `lease_poll_seconds = 1`

La espera adaptativa significa que una segunda ejecución puede esperar ownership dentro del presupuesto seguro restante, reservando al menos un `iteration_timeout_seconds` completo para trabajo útil. Esperar lease consume el mismo presupuesto total de la ejecución; adquirir ownership nunca entrega una ventana completa nueva.

Una segunda ejecución que encuentra el lease ocupado espera sin preparar WebIDs, consultar PI ni escribir estado o datasets. Si el dueño libera el lease de forma limpia, la siguiente ejecución puede adquirirlo en el siguiente polling. Si el dueño muere sin liberar, el heartbeat deja de renovarse y la siguiente ejecución puede recuperar el lease cuando expire.

El despliegue esperado usa `parallelism = 1`. El lease sigue siendo la protección de integridad frente a solapamientos accidentales o ejecuciones abandonadas.

## Ventana operativa

El process mantiene:

- polling local de 1 segundo;
- `execution_timeout_seconds = 600` como presupuesto total del runtime;
- `shutdown_grace_seconds = 10`;
- ventana segura de trabajo/adquisición de 590 segundos;
- schedule de Azure cada 10 minutos;
- `replicaTimeout = 610` como hard timeout externo recomendado.

`replicaTimeout` pertenece a la configuración de despliegue y no se define en el código del process. Un owner sano debe terminar de forma cooperativa dentro del presupuesto Atlanticus; si queda bloqueado, el hard timeout externo puede terminarlo, tras lo cual el heartbeat deja de renovarse y otra ejecución recupera ownership.

## Adquisición PI

Los límites `points_max_paths`, `interpolated_max_web_ids` y `recorded_max_web_ids` pertenecen a la integración y son validados por ella. El process es responsable de dividir el trabajo antes de llamar a la integración.

`PI_WEB_API_MAX_DATA_POINTS` es una guarda de planificación del process para requests interpolated. Su valor inicial es `150000`; no se incorpora al cliente PI genérico.
La estimación incluye el punto del límite derecho que PI puede devolver aunque luego se descarte, por lo que una request interpolated se dimensiona como `(slots + 1) * web_ids`. El process verifica además el tamaño real de cada respuesta. Esto cubre especialmente `RECORDED`, cuya cantidad de eventos no se puede predecir antes de consultar PI.

Una ventana puede dividirse adicionalmente cuando una llamada falla por conexión distinta de timeout, por un HTTP recuperable (`408`, `425`, `429` o `5xx`) o cuando la respuesta real supera `PI_WEB_API_MAX_DATA_POINTS`. Solo se vuelve a intentar la porción fallida. El split continúa hasta ventanas de aproximadamente 60 segundos; si una respuesta sigue excediendo el límite en la ventana mínima, la adquisición falla de forma explícita. Los timeouts de transporte siguen la política específica descrita más abajo y no provocan split después de agotar sus retries. Errores de autenticación, configuración, request local o estructura de respuesta no se degradan mediante split.

El endpoint se consulta hasta `last_slot + interpolation_seconds` para poder representar correctamente un único slot y los eventos recorded de la última celda. Cualquier dato devuelto en el límite derecho se descarta antes de materializar.

## Idempotencia y proyección

Para `INTERPOLATED`, la identidad lógica final es `slot_timestamp_utc`, materializada físicamente en la columna `timestamp_utc` y alineada al eje de slots del catálogo. Duplicados exactos de `tag + timestamp` usan el último valor recibido y los conflictos se observan.

Para `RECORDED`, los eventos se deduplican primero por `(tag_name, native_timestamp_utc)`. Si PI entrega la misma identidad con valores distintos, el último valor recibido gana y el conflicto queda registrado. Luego los eventos se proyectan al eje común de slots. Si distintos eventos del mismo tag caen en un mismo slot, gana el evento de timestamp nativo más reciente y la colisión queda observable.

La materialización final de `DAILY` y `MONTHLY` usa merge idempotente por `timestamp_utc`. `LATEST` usa reemplazo completo y solo está permitido para interpolated por el contrato del catálogo.

## Evolución de schema

Una partición `DAILY` o `MONTHLY` ya existente conserva las columnas con las que fue abierta. Tags nuevos agregan columnas y las filas antiguas reciben `null`. Tags retirados permanecen en esa partición y las filas nuevas reciben `null`.

Una partición nueva se crea exclusivamente con el catálogo vigente, por lo que columnas retiradas desaparecen naturalmente al cambiar de archivo. `LATEST`, al no tener rollover de partición, refleja siempre el catálogo vigente.

Para evitar cargar archivos completos solo para conocer sus columnas, `DatasetRuntime.read_schema()` consulta únicamente el schema confirmado del target.

## Orden de commit

El orden seguro de cada iteración es:

1. adquirir y conservar ownership del lease;
2. completar la preparación de WebIDs y reutilizarla durante el resto de la ejecución;
3. planificar la ventana pendiente desde el producer watermark;
4. adquirir y deduplicar datos PI;
5. comprobar que el runtime siga dentro de su ventana segura;
6. materializar todos los datasets de forma idempotente, comprobando cancelación antes de iniciar cada escritura;
7. comprobar nuevamente cancelación;
8. publicar `source_watermark_utc`;
9. confirmar `committed_watermark_utc`.

Si ocurre un crash después de una escritura Parquet y antes de los watermarks, la siguiente ejecución repite la ventana. El merge/reemplazo idempotente evita duplicados y se prefiere replay antes que avanzar estado dejando un hueco silencioso.
## Timeouts transitorios de PI Web API

Los timeouts de transporte de PI Web API se tratan como una degradación temporal de la dependencia, no como una autorización para confirmar datos incompletos. Cada solicitud dispone de un intento inicial y hasta tres reintentos explícitos con pausas de 2, 3 y 5 segundos. La política vive en el process; `atlanticus-http` permanece sin reintentos implícitos.

La misma política cubre la resolución de WebIDs y las lecturas `streamsets`. Si una solicitud se recupera dentro de esos reintentos, la iteración continúa normalmente. Si los tres reintentos se agotan, la iteración termina con `outcome=skipped` y `reason=pi_timeout`: no se publica Parquet y no avanzan ni el source watermark ni el producer watermark. El runtime permanece vivo y puede iniciar otra iteración inmediatamente según su presupuesto restante.

Mientras el timeout persista, las iteraciones siguientes vuelven a intentar desde el mismo watermark confirmado. Cuando PI vuelve a estar disponible, el planner observa el gap acumulado y recupera la ventana pendiente de forma acelerada, dentro del horizonte máximo de recuperación y de los límites de puntos ya definidos. Errores no clasificados como timeout, como autenticación inválida, catálogo inválido, schema inválido o fallos de materialización, continúan propagándose como errores reales.

