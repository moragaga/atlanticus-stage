# Diseño de `atlanticus-http==0.1.0`

## Frontera

El paquete convierte configuración explícita en una conexión HTTP síncrona reutilizable. Entrega
respuestas neutrales o transfiere contenido a un stream; la capa consumidora interpreta la API.

```text
settings inyectados
→ HttpClient con pool único
→ una solicitud HTTP
→ HttpResponse o HttpStreamResult
→ adaptador de API
```

PI Web API será un adaptador posterior. Allí vivirán endpoints, WebIds, payloads, validaciones y
reintentos selectivos por timeout. El transporte genérico no puede decidir si repetir un `POST` es
seguro.

## Cliente y ciclo de vida

`HttpClient` crea de forma perezosa un `httpx.Client`. Todas las solicitudes de la instancia
comparten su pool hasta cerrar el contexto. Después de `close()` la instancia no puede reabrirse;
esto evita usar accidentalmente un pool cuyo ciclo de vida ya terminó.

HTTP/1.1 es suficiente para el contrato inicial. No se habilita HTTP/2 ni async sin un caso real.
Los redirects no se siguen: una respuesta `3xx` es un status no exitoso y debe resolverse en la
capa que conoce la API.

## Autenticadores

`none`, `bearer` y `basic` son combinaciones cerradas. Los settings rechazan secretos sobrantes o
faltantes y el request no permite reemplazar `Authorization`. Bearer recibe un token ya resuelto;
obtenerlo o renovarlo pertenece a otra capa.

NTLM y Kerberos requieren librerías, negociación y pruebas de infraestructura distintas. Se
incorporarán como autenticadores separados sólo si una integración real los necesita.

## Memoria y streaming

`request()` es apropiado cuando el consumidor acepta cargar el cuerpo completo. Su límite por
defecto es `64 MiB`: primero revisa `Content-Length` y luego cuenta los bytes efectivos mientras
lee bloques de 64 KiB. Así también se acotan respuestas sin tamaño declarado. `stream_to()` usa el
iterador de bytes del SDK y escribe cada bloque directamente. El paquete no mantiene una copia
paralela ni interpreta Parquet.

El stream pertenece al consumidor. Si una escritura es parcial, se detiene la transferencia y se
reporta el número de bytes confirmados. La eliminación o reutilización de ese destino parcial no
pertenece al cliente HTTP.

## Errores seguros

| Condición | Error neutral |
|---|---|
| Configuración incompatible | `HttpConfigurationError` |
| Request local inválido | `HttpRequestError` |
| Red o protocolo | `HttpConnectionError` |
| Timeout | `HttpTimeoutError` + fase |
| Status fuera de `2xx` | `HttpStatusError` + método/status |
| JSON, texto, tamaño o metadato de respuesta inválido | `HttpResponseError` |
| Stream interrumpido después de escribir | `HttpStreamError` + bytes |

No se encadenan errores de HTTPX que puedan conservar el request. Tampoco se incluyen URL, query,
response body o headers en las excepciones neutrales.

Si cerrar una respuesta o el pool falla mientras ya existe otro error, se conserva el error
original. Si el cierre era la única operación fallida, se informa mediante un error neutral.

## Contratos estrictos

La construcción directa de `HttpSettings` recibe valores finales: `HttpAuthMode`, booleanos reales,
números finitos y un límite entero positivo. `from_mapping()` es la única frontera que convierte
texto de configuración. Los modelos de respuesta validan status, método, headers, contenido y
conteos incluso cuando un consumidor los construye directamente.

El decodificador JSON acepta cualquier tipo raíz válido, pero exige UTF-8, números finitos y claves
únicas dentro de cada objeto. Un payload ambiguo no se entrega al conector especializado.

## Observabilidad

Los eventos `http.request` y `http.stream_to` proyectan modo de autenticación, sufijo lógico,
método, status y tamaño. No proyectan destino, endpoint, parámetros, headers, cuerpo ni secretos.
Los errores que no pertenecen al catálogo neutral se convierten mediante el sanitizador de
`atlanticus-observability`; sus mensajes originales no se copian.

## Prueba de integración

La Fake API usa sólo la biblioteca estándar y corre en un servicio separado. Las tres familias de
rutas entregan las mismas capacidades y validan el header esperado. Un identificador de conexión
demuestra que dos solicitudes consecutivas reutilizan el socket HTTP/1.1. La ruta lenta mantiene
un contador: después de un timeout debe existir exactamente una llamada.

El runner instala el wheel real y ejecuta en `linux/amd64`, igual que Blob y Service Bus.
