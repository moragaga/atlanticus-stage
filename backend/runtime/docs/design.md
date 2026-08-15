# Diseño de `atlanticus-job-runtime`

## Frontera

Runtime orquesta ejecución, coordinación y cierre. Observability registra lo ocurrido; cada proceso
inyecta su lógica y compone sus conectores. Runtime no conoce ADA, Azure Container Apps, cron,
`config.json`, Cosmos, SQL, Blob, Service Bus, Databricks, State ni Datasets.

La API pública se limita a `JobDefinition`, `JobRuntimeContext`, `RuntimeConfiguration`,
`RuntimeExecutionResult`, `execute_job` y los errores controlados. Lease, argumentos y recursos
son detalles internos.

## Presupuesto temporal

`execution_timeout_seconds` es el presupuesto total del runtime desde que crea el contexto de la
ejecución. La adquisición y espera de la lease consume ese mismo presupuesto. Nunca se agrega una
ventana completa nueva después de obtener ownership.

`shutdown_grace_seconds` se reserva al final de ese presupuesto. Por eso
`safe_execution_seconds = execution_timeout_seconds - shutdown_grace_seconds` representa el tiempo
máximo disponible para adquisición de lease, iteraciones y sleeps antes de iniciar el cierre.

`lease_wait_seconds=None` activa la política adaptativa: la ejecución puede esperar ownership con
el presupuesto seguro restante, reservando al menos un `iteration_timeout_seconds` completo para
trabajo útil. Un valor numérico actúa como cap adicional, pero nunca puede extender la ejecución
más allá de su deadline ni consumir la reserva mínima de una iteración.

La plataforma externa conserva su propio hard timeout. Runtime no lo calcula ni lo lee desde
configuración de despliegue.

## Secuencia

1. valida definición, argumentos y configuración;
2. crea identidad y contexto, fijando deadline y ventana segura;
3. calcula la espera efectiva y adquiere la lease por `job_key`;
4. configura observabilidad local y, opcionalmente, Azure;
5. registra cuánto tiempo consumió la adquisición y si recuperó un owner expirado;
6. inicia el heartbeat;
7. instala temporalmente el handler cooperativo de `SIGTERM`;
8. ejecuta iteraciones y verifica la lease antes y después de cada una;
9. cierra el monitor y emite el resumen final;
10. restaura la señal, limpia memoria, libera la lease y cierra observabilidad.

La lease se adquiere antes de abrir la traza persistente porque protege el supuesto de un único
escritor por servicio. Se libera antes de cerrar observabilidad para poder registrar un fallo de
cierre sin ocultar un error de negocio previo.

## Coordinación

Los identificadores utilizados en rutas se validan y nunca se transforman. Así, valores distintos
como `job/key` y `job_key` no pueden converger en el mismo archivo.

El heartbeat renueva la expiración en un hilo interno. Si pierde el token propietario o no puede
confirmar una escritura segura, conserva un error controlado, despierta el contexto y evita que la
ejecución termine como exitosa. La duración del lease no depende de la duración total del job: un
job largo conserva ownership renovando heartbeats; un owner muerto queda recuperable cuando vence
su lease.

La detención sigue siendo cooperativa: una iteración bloqueada solo responde cuando su dependencia
retorna o vence su timeout.

## Cancelación

`RuntimeCancellationRequested`, `KeyboardInterrupt` y `SIGTERM` generan
`execution.cancelled`, no `execution.failed`. La primera razón de detención se conserva y
`JobRuntimeContext.wait()` puede despertarse inmediatamente.

El agotamiento de la ventana segura y la falta de tiempo para una nueva iteración son cierres
normales. El timeout forzoso final pertenece a la plataforma de ejecución.

## Recursos

El soporte de recursos está separado en modelos, sampler cgroup, detector de presión y monitor.
Las muestras solo actualizan acumuladores en memoria; no se conserva una serie temporal. Una falla
de muestreo se registra una vez como warning hasta que exista una muestra correcta y nunca rompe la
lógica del job.

Los episodios de presión y sus escalamientos requieren muestras consecutivas. Esto evita escalar
por picos alternados que no representan presión sostenida.

## Seguridad

Las excepciones automáticas se convierten mediante `ErrorInfo.from_exception()`. Se conserva el
tipo y la ubicación del fallo, pero no `str(error)`, URLs firmadas, connection strings ni mensajes
de SDK. La configuración proyectada hacia la extensión Azure contiene únicamente sus tres
variables explícitas.
