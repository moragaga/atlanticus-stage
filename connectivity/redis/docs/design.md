# Diseño de `atlanticus-redis`

## Boundary

`atlanticus-redis` representa una conexión Redis standalone síncrona. Los nombres de conexión, prefixes y la resolución de secretos pertenecen a composición.

```text
configuration / Key Vault
          |
          v
process composition
          |
          +-- runtime -> RedisSettings(url, username, password, ...)
          +-- cache   -> RedisSettings(url, username, password, ...)
          |
          v
atlanticus.connectivity.redis
```

## Conexión inicial

El contrato inicial separa direccionamiento de autenticación:

```text
url       -> redis://host:port | rediss://host:port
username  -> usuario Redis final
password  -> secreto Redis final
database  -> índice lógico separado de la URL
```

No se acepta `redis://user:password@host/db`. El objetivo es impedir que endpoint y secreto se mezclen en una URI sensible y mantener simple la composición con Key Vault o cualquier otra fuente de configuración.

`rediss://` activa TLS. `redis://` sólo es válido con `allow_insecure_transport=True`.

Entra ID, credential providers, Sentinel y Cluster quedan fuera de esta primera versión. Pueden incorporarse después mediante contratos adicionales si aparece un consumidor real.

## Datos

El conector trabaja con keys de texto y valores binarios. No decide encoding ni formato. `get` retorna `bytes | None`; `mget` retorna una tupla posicional de `bytes | None`.

## TTL

`RedisTtl` evita colapsar los tres estados que Redis representa con `TTL`:

- key ausente: `exists=False`, `seconds=None`;
- key persistente: `exists=True`, `seconds=None`;
- key con expiración: `exists=True`, `seconds>=0`.

## Operación y costo

- `health_check_interval=0`: sin PING periódico implícito.
- RESP2 explícito: contrato de respuestas estable.
- retries configurados en cero: Atlanticus no oculta reintentos de red.
- `max_connections` limita crecimiento del pool.
- `max_mget_keys` limita cardinalidad accidental de operaciones multi-key.
