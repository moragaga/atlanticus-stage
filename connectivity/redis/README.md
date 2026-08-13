# atlanticus-redis

Conector Redis síncrono, genérico y reutilizable para Atlanticus.

## Responsabilidad

El paquete recibe un `RedisSettings` final ya compuesto y ofrece operaciones key/value binarias. No conoce prefixes de variables, nombres de conexiones, Key Vault, ADA ni reglas de negocio.

La capa superior puede componer múltiples clientes nombrados con settings independientes, por ejemplo `runtime`, `cache` o cualquier otro nombre propio de la aplicación.

## Contrato de conexión inicial

La primera versión usa tres valores explícitos:

- `url`: endpoint Redis sin credenciales, database, query ni fragment;
- `username`: usuario Redis final;
- `password`: password final, preservado exactamente y excluido de `repr`.

Ejemplos válidos:

```text
rediss://redis.example.com:6380
redis://redis-server:6379
```

La URL no puede contener `username` ni `password`. La composición resuelve endpoint y secretos por separado antes de construir `RedisSettings`.

`rediss://` habilita TLS. `redis://` requiere `allow_insecure_transport=True` de forma explícita y está orientado a escenarios controlados como Docker local.

## Seguridad

- El password se conserva exactamente y se excluye de `repr`.
- Endpoint y credencial nunca se fusionan en una connection string sensible.
- Los errores públicos se sanitizan y no encadenan excepciones del SDK.
- RESP2 es explícito.
- No hay retries del SDK.
- `health_check_interval=0` evita PINGs periódicos implícitos.

## Contrato operacional

`RedisClient` expone `health_check`, `get`, `set`, `delete`, `exists`, `expire`, `ttl`, `mget` y `close`.

Los payloads son bytes. Serialización JSON, compresión, modelos de alarmas, locks, Pub/Sub, Streams y otras estructuras quedan fuera de este conector hasta existir un consumidor que las requiera.

El cliente interno mantiene un connection pool acotado por `max_connections`; `mget` está acotado por `max_mget_keys`.
