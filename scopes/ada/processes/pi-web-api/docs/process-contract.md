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

## Idempotencia de replay

El watermark puede quedar detrás del dataset si una ejecución muere después de materializar y antes de confirmar estado. Por diseño, la siguiente ejecución puede volver a consultar y materializar la misma ventana. Ese replay debe ser idempotente.

Para `INTERPOLATED`, la identidad final de una fila es `slot_timestamp_utc`. Si el slot ya existe en el archivo activo, no se agrega una segunda fila.

Para `RECORDED`, los eventos recibidos desde PI se deduplican primero por `(tag_name, native_timestamp_utc)`. Si PI entrega la misma identidad con valores distintos, el último valor recibido gana y el conflicto debe quedar registrado en observabilidad. Después los eventos se proyectan al eje común de slots y la materialización también debe ser idempotente por `slot_timestamp_utc`.

La deduplicación y la materialización idempotente pertenecen al siguiente incremento del process; este documento fija el contrato antes de implementarlas.

## Orden de commit

Cuando exista materialización real, el orden seguro será:

1. adquirir y conservar ownership del lease;
2. adquirir/procesar la ventana;
3. verificar que el runtime siga saludable antes de comenzar escrituras nuevas;
4. materializar datasets de forma idempotente;
5. publicar `source_watermark_utc`;
6. confirmar `committed_watermark_utc`.

Se prefiere repetir una ventana después de un crash antes que avanzar estado y dejar un hueco silencioso.
