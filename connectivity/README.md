# Atlanticus Connectivity

Connectivity reúne contratos e implementaciones de servicios externos que pueden ser usados por
backend y web. No contiene reglas de negocio, transformaciones de datasets, KPI, alarmas ni UI.

## Packages

```text
atlanticus-blob          # activo: 0.1.0
atlanticus-service-bus   # activo: 0.1.0
atlanticus-http          # activo: 0.1.0; carpeta física http-client
atlanticus-sql           # activo: 0.1.0
atlanticus-cosmos        # activo: 0.1.0
atlanticus-redis         # activo: 0.1.0
atlanticus-databricks
atlanticus-identity
atlanticus-key-vault
```

Cada conector tiene una carpeta directa y un wheel independiente. `connectivity/` ya es un workspace
activo porque Blob aporta el primer contrato real, su lock y su batería propia.

Los imports públicos iniciales son:

```python
from atlanticus.connectivity.blob import BlobService
from atlanticus.connectivity.http import HttpClient
from atlanticus.connectivity.service_bus import ServiceBusTopicReceiver
from atlanticus.connectivity.sql import SqlClient
from atlanticus.connectivity.cosmos import CosmosClient
from atlanticus.connectivity.redis import RedisSnapshotStore
```

Service Bus recibe como máximo una entrega por llamada, conserva el receiver abierto y procesa
varias entregas secuencialmente después de resolver cada lock. No retiene lotes ni datasets en
memoria. La interpretación de NotPII y la descarga Blob pertenecen al futuro adaptador de fuente.

HTTP mantiene un cliente síncrono reutilizable, autenticación explícita `none`, `bearer` o `basic`,
cuatro timeouts y streaming hacia un destino del consumidor. Ejecuta un solo intento: las políticas
de reintento pertenecen al adaptador que conoce la API. La carpeta se llama `http-client` para no
ocultar el módulo estándar `http`, pero el wheel e import conservan `atlanticus-http` y
`atlanticus.connectivity.http`.

SQL recibe una connection string completa, entrega filas neutrales y separa resultados pequeños
acotados de streams por `fetchmany()`. Usa `mssql-python`, acepta secretos legacy que declaren ODBC
Driver 17 o 18 sin usar ese número como selector del runtime, no depende de Pandas o SQLAlchemy y no
reintenta. La garantía de sólo lectura proviene de los permisos reales del usuario SQL.

Cosmos mantiene un cliente síncrono reutilizable con create, read, find, upsert, patch, delete, ETag,
consultas documentales y `SELECT VALUE`. Separa materialización acotada, páginas con continuation token e iteradores sin
límite global. El provisionamiento de base y contenedores es explícito, idempotente y no modifica TTL
por sorpresa. No depende de Pandas.

Redis conserva el último snapshot JSON por `application + channel`. Cada publicación genera una
versión UTC nueva mediante Lua, reemplaza el hash y aplica TTL o `PERSIST` atómicamente. El backend
decide si existe un cambio antes de publicar. No guarda historial, no compara contenido y limita
cada payload serializado a 10 MiB. La carpeta física se llama `redis-store` para no ocultar la
dependencia externa `redis`, mientras el wheel e import conservan `atlanticus-redis` y
`atlanticus.connectivity.redis`.

## Regla de diseño

Una conexión entrega capacidades técnicas al consumidor. Por ejemplo, SQL puede leer una consulta
y Cosmos puede operar documentos; la selección de una tabla de Dispatch o la estructura de un KPI
pertenece al backend o a la solución que consume esa conexión.

Los settings reciben valores inyectados por la composición. Los conectores no consultan secretos ni
se incorporan a `atlanticus-job-runtime`.
