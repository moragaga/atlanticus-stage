# Diseño de `atlanticus-observability`

## Flujo

1. El consumidor crea un `ObservabilityEvent` neutral.
2. `Observability` completa el contexto activo y sanitiza al materializar.
3. `CompositeEventSink` entrega el mismo objeto a destinos independientes.
4. Cada destino filtra y proyecta antes de persistir o enviar.

El consumidor nunca decide qué campos llegan a Azure. Esa política pertenece al sink desplegado.

## Modelo de fallos

`emit_event` es fail-safe: un error de trazabilidad no interrumpe el proceso. `runtime_guard` mide el
contrato externo, emite success/error y vuelve a entregar exactamente el resultado o la excepción
original. La conversión automática de excepciones conserva tipos y ubicación, pero nunca copia el
mensaje de la excepción. Un mapper explícito puede aportar mensajes seguros y una proyección
desplegada puede eliminar el traceback.

## Recursos

El monitor ejecuta un único hilo daemon de infraestructura. No es un worker de negocio. Actualiza
acumuladores constantes en memoria, detecta transiciones sostenidas y descarta cada muestra. Un
checkpoint permite forzar la revisión al final de una iteración, aun si el timeout general pudiera
impedir esperar el siguiente segundo. El checkpoint participa en la detección de presión de
memoria y conserva CPU como estadística, pero la presión de CPU sólo cambia con muestras periódicas
para evitar alertas causadas únicamente por el pico de cierre. Las métricas de episodios se
identifican por recurso para que los acumulados diarios de memoria y CPU permanezcan separados.

## Persistencia

La raíz persistente se deriva como `${VOLUMEN_PATH}/${APPLICATION}/logs`. Debajo se utiliza
`${SERVICE}/day=YYYY-MM-DD`. La raíz diaria conserva cierres de ejecuciones, iteraciones con trabajo,
incidencias y el acumulado. `latest.json` permanece en la raíz del servicio. `run_id` correlaciona
los registros de una ejecución, pero no crea carpetas ni archivos separados. Un nuevo contenedor
del mismo servicio relee los snapshots y continúa el mismo historial diario.

Ambiente, servicio y `run_id` se proyectan en los registros operacionales. El payload técnico
completo no se agrega a un historial general.

La implementación es dueña únicamente de `${APPLICATION}/logs/${SERVICE}` y sólo elimina hijos con
el patrón exacto `day=YYYY-MM-DD` pertenecientes a semanas ISO anteriores. Nunca purga otros
dominios de la aplicación ni `latest.json`.

El contrato exige un único escritor activo por aplicación y servicio. La coordinación pertenece a
job-runtime: adquiere una lease antes de configurar el sink y la mantiene hasta terminar los
snapshots. Si Azure corta una réplica, el siguiente runtime recupera la lease expirada y emite
`execution.timed_out` para el run anterior. Observability recibe ese evento, pero no decide cuándo
un lock está vigente o expirado.

La integración prevista utilizará `JobCapability.RUNTIME_STORAGE` y esta configuración inicial:

```text
schedule interval:          360 s
internal execution timeout: 330 s
shutdown grace:              15 s
Azure replica timeout:      350 s
parallelism:                  1
replica completion count:     1
replica retry limit:          0
```

La lease se conserva durante el cierre. Si el siguiente contenedor encuentra una lease expirada,
la recupera y ejecuta; no omite el run sólo porque el archivo anterior continúe en el storage.

## Extensiones previstas

- adaptador Azure con selección mínima de eventos;
- integración del monitor, lifecycle y lease de ejecución en `atlanticus-job-runtime`;
- catálogos de connectivity que apliquen `runtime_guard` a Cosmos, SQL, Blob, Redis y Service Bus;
- evaluación posterior de concurrencia con telemetría por tarea y destino.
