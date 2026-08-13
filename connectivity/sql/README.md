# atlanticus-sql

Conector síncrono y neutral para lecturas de SQL Server y Azure SQL mediante `mssql-python`.
La versión `0.1.0` recibe una connection string completa, ejecuta una sola vez cada consulta y
entrega tuplas inmutables sin depender de Pandas, PyArrow o SQLAlchemy.

## Instalación

```bash
uv add atlanticus-sql==0.1.0
```

El wheel instala `mssql-python==1.13.0`, que obtiene su runtime ODBC mediante el paquete companion
`mssql-python-odbc`. Atlanticus no instala `unixODBC`, `msodbcsql17` ni `msodbcsql18` en el host.
En Debian/Ubuntu el runtime requiere `libltdl7`, `libkrb5-3` y `libgssapi-krb5-2`.

## Configuración

```python
from atlanticus.connectivity.sql import SqlSettings

settings = SqlSettings(
    connection_string=(
        'SERVER=sql.internal,1433;'
        'DATABASE=dispatch;'
        'UID=reader;'
        'PWD=secret;'
        'Encrypt=yes;'
        'TrustServerCertificate=no;'
        'Connection Timeout=5;'
    ),
    query_timeout_seconds=60,
    batch_size=10_000,
    max_query_rows=10_000,
)
```

La connection string es una unidad inyectada y se conserva exactamente como fue recibida; el
paquete no aplica `strip()` al secreto almacenado ni cambia destino, autenticación, cifrado,
certificado ni otros valores. Su valor queda excluido de `repr`, errores y observabilidad.

Para permitir una migración sin modificar secretos existentes, también se aceptan estas propiedades
legacy:

```text
Driver={ODBC Driver 17 for SQL Server}
Driver={ODBC Driver 18 for SQL Server}
Connection Timeout=<segundos>
```

`mssql-python` controla internamente su propio ODBC Driver 18 y rechaza que `Driver` sea suministrado
por el usuario. Atlanticus valida que el valor legacy sea 17 o 18 y retira únicamente esa propiedad
antes de abrir la conexión. `mssql-python` tampoco acepta `Connection Timeout` dentro de su cadena;
Atlanticus lo retira sólo de la copia efímera y lo reenvía mediante `connect(timeout=...)`. El valor
del secreto almacenado no se modifica y el número legacy no selecciona el driver real del runtime.

También puede construirse desde un mapping ya resuelto por la composición:

```python
settings = SqlSettings.from_mapping(
    values={
        'SQL_CONNECTION_STRING_DISPATCH': '...',
        'SQL_QUERY_TIMEOUT_SECONDS_DISPATCH': '60',
        'SQL_BATCH_SIZE_DISPATCH': '10000',
        'SQL_MAX_QUERY_ROWS_DISPATCH': '1000',
    },
    suffix='DISPATCH',
)
```

El paquete no consulta directamente variables de entorno.

## Resultado pequeño

```python
from atlanticus.connectivity.sql import SqlClient

client = SqlClient(settings=settings)
result = client.query(
    'SELECT shift_id, tonnage FROM dbo.shift_dumps WHERE shift_id = ?',
    ('20260722001',),
)

print(result.columns)
print(result.rows)
```

Los parámetros son posicionales y corresponden a marcadores `?`. Nunca deben interpolarse
credenciales ni valores dentro del SQL.

`query()` solicita como máximo `max_query_rows + 1` filas. Si el resultado excede el límite, lanza
`SqlResultLimitError` sin cargar el resto y el consumidor debe cambiar a `iter_batches()`.

## Resultado grande por lotes

```python
with client.iter_batches(
    'SELECT shift_id, tonnage FROM dbo.shift_dumps ORDER BY shift_id',
    batch_size=10_000,
) as batches:
    for batch in batches:
        procesar(batch)
```

El stream conserva una conexión durante esa consulta, usa `fetchmany()` y la cierra al agotarse o
al salir de `with`. Cada nueva operación abre otra conexión; `mssql-python` mantiene pooling interno.

Un adaptador que realmente necesite Pandas lo incorpora por lote. Pandas y PyArrow no son
dependencias de `atlanticus-sql`.

## Límites de `0.1.0`

- SQL Server y Azure SQL mediante `mssql-python==1.13.0`.
- Compatibilidad de entrada con secretos legacy que declaren ODBC Driver 17 o 18.
- Autenticación y TLS definidos por la connection string.
- API pública orientada a lectura: `health_check()`, `query()`, `table_change_markers()` e
  `iter_batches()`.
- Sin API de escritura, transacciones públicas, `executemany()` ni DataFrames.
- Sin reintentos automáticos.
- Sólo el primer result set de una consulta.
- `Connection Timeout` legacy se traduce al argumento de conexión de `mssql-python`; el timeout de
  consulta permanece en `SqlSettings`.
- `UNIQUEIDENTIFIER` se devuelve como `str` para conservar compatibilidad del contrato.
- La cuenta de producción debe poseer permisos reales de sólo lectura; el cliente no interpreta SQL.

Windows Integrated, políticas de reintento y una API Arrow explícita quedan fuera de esta versión.
