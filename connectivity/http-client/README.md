# `atlanticus-http`

`atlanticus-http==0.1.0` entrega transporte HTTP síncrono y genérico con conexión reutilizable,
timeouts estrictos y respuestas neutrales. El import público es:

```python
from atlanticus.connectivity.http import HttpClient, HttpSettings
```

La carpeta física se llama `http-client` para no ocultar el paquete estándar `http` cuando los
comandos se ejecutan desde `connectivity/`. Esto no modifica el nombre del wheel ni el import.

## Frontera

El cliente conoce HTTP, autenticación y streaming. No conoce PI Web API, endpoints concretos,
estructuras JSON de dominio, datasets ni políticas de reintento:

```python
settings = HttpSettings.from_mapping(
    values=configuration.values,
    suffix='PI_WEB_API',
)

with HttpClient(settings=settings) as client:
    payload = client.request_json(
        'GET',
        'streamsets/interpolated',
        params={'startTime': start_time, 'endTime': end_time},
    )
```

Una instancia conserva un único `httpx.Client` y su pool hasta `close()`. Los endpoints siempre son
relativos a `base_url`; query parameters se entregan mediante `params`.

## Autenticación explícita

`AUTH_MODE` es obligatorio al construir desde un mapping y nunca se infiere por secretos presentes:

| Modo | Configuración requerida | `Authorization` |
|---|---|---|
| `none` | Sin credenciales | Ausente |
| `bearer` | `BEARER_TOKEN` | `Bearer <token>` |
| `basic` | `USERNAME` y `PASSWORD` | HTTP Basic |

Una combinación incompatible falla antes de abrir la conexión. El token, usuario y contraseña usan
`repr=False` y no se proyectan a observabilidad.

Las claves sin sufijo son:

```text
HTTP_BASE_URL
HTTP_AUTH_MODE
HTTP_BEARER_TOKEN
HTTP_USERNAME
HTTP_PASSWORD
HTTP_CONNECT_TIMEOUT_SECONDS
HTTP_READ_TIMEOUT_SECONDS
HTTP_WRITE_TIMEOUT_SECONDS
HTTP_POOL_TIMEOUT_SECONDS
HTTP_MAX_RESPONSE_BYTES
HTTP_VERIFY_TLS
HTTP_ALLOW_INSECURE_HTTP
```

Con `suffix='PI_WEB_API'`, cada clave termina en `_PI_WEB_API`.

## Respuestas

`request()` retorna `HttpResponse` con status, método, headers y bytes. El cuerpo y los headers no
aparecen en `repr`; los nombres de headers se normalizan a minúsculas. Los helpers son:

- `request_json()` para cualquier valor JSON válido;
- `request_text()` con codificación explícita, UTF-8 por defecto;
- `request_bytes()` para respuestas acotadas;
- `stream_to()` para transferir bloques directamente a un stream del consumidor.

Las respuestas cargadas en memoria tienen un máximo de `64 MiB` por defecto, configurable con
`HTTP_MAX_RESPONSE_BYTES`. El cliente valida `Content-Length` cuando existe y también cuenta los
bytes realmente recibidos. El límite no se aplica a `stream_to()` porque esa operación no acumula
el cuerpo.

`stream_to()` no acumula la respuesta completa. Si el transporte falla después de escribir una
parte, el error conserva únicamente `bytes_transferred` para que el consumidor descarte o gestione
su archivo parcial.

## Timeouts y reintentos

Los presupuestos de conexión, lectura, escritura y espera del pool son independientes. Un timeout
produce `HttpTimeoutError` con una de estas fases:

```text
connect | read | write | pool
```

Cada llamada realiza exactamente un intento. `atlanticus-http` no repite automáticamente `GET`,
`POST` ni ninguna otra operación. Una capa especializada como PI Web API puede capturar
`HttpTimeoutError` y aplicar uno o dos reintentos cuando su semántica lo permita.

## Seguridad

- TLS se verifica por defecto.
- Una URL `http://` exige `ALLOW_INSECURE_HTTP=true`.
- La URL base no acepta credenciales, query ni fragmento.
- No se siguen redirects automáticamente.
- El entorno no puede inyectar proxies de forma implícita.
- Un request no puede sobrescribir `Authorization` mediante headers locales.
- Los errores no incluyen URL, query, headers, cuerpo ni excepciones internas de HTTPX.
- JSON rechaza claves duplicadas, `NaN`, infinitos y contenido que no sea UTF-8.
- La construcción directa de settings exige enums, booleanos y números con sus tipos finales.

## Validación

Unitarios, lint, formato, espejo y wheel:

```bash
cd connectivity
./scripts/check.sh
```

Fake API real en Docker `linux/amd64`:

```bash
cd connectivity/docker
./03_http_integration_test.sh
```

La Fake API prueba rutas equivalentes `public`, `bearer` y `basic`; credenciales inválidas y
cruzadas; JSON, texto, bytes, streaming, `POST`, pooling, timeouts, `4xx`, `5xx` y ausencia de
reintentos.

## Fuera de alcance de `0.1.0`

- lógica, rutas y modelos de PI Web API;
- reintentos, backoff y circuit breaker;
- obtención o renovación de Bearer Tokens;
- NTLM, Kerberos y autenticación integrada de Windows;
- redirects automáticos;
- multipart, WebSocket y API asíncrona;
- Identity y Key Vault.

El diseño detallado se encuentra en [`docs/design.md`](docs/design.md).
