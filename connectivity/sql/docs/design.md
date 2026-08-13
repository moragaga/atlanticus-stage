# Diseño de atlanticus-sql 0.1.0

## Frontera

`atlanticus-sql` resuelve transporte SQL y materializa filas neutrales. No conoce tablas de
Dispatch, Blockgrade o ADA; tampoco transforma datos, calcula KPI ni decide políticas de reintento.

```text
adaptador de fuente
    -> SqlClient
        -> mssql-python 1.13.0
            -> mssql-python-odbc 18.6.2.1
                -> SQL Server / Azure SQL
```

PyArrow no forma parte del contrato productivo. Los consumidores que requieran una representación
analítica la construyen fuera de connectivity.

## Connection string

El despliegue entrega una connection string completa. `SqlSettings` conserva su valor original con
`repr=False`, sin aplicar `strip()` al secreto, y nunca lo incluye en observabilidad ni errores.

`mssql-python` reserva la propiedad `Driver` y agrega internamente `ODBC Driver 18 for SQL Server`.
Para conservar secretos ya existentes, Atlanticus implementa una compatibilidad mínima: acepta
`Driver={ODBC Driver 17 for SQL Server}` o `Driver={ODBC Driver 18 for SQL Server}`, valida que no
haya duplicados y retira sólo esa propiedad al abrir la conexión. `Connection Timeout=<segundos>`
también se acepta como compatibilidad de secretos existentes: se retira de la copia efímera y se
reenvía como argumento `timeout` de `mssql-python`. El resto de la cadena se conserva. Un driver
legacy distinto o un timeout inválido falla en configuración en vez de ser reinterpretado
silenciosamente.

El parser de esa frontera sólo segmenta propiedades respetando valores entre llaves; no normaliza
TLS, autenticación, servidor, base, usuario, contraseña ni otros parámetros.

## Memoria

`query()` es una operación acotada. Ejecuta `fetchmany(max_query_rows + 1)` para detectar el exceso
sin depender de `cursor.rowcount`. Al superar el límite cierra los recursos y lanza
`SqlResultLimitError`.

`iter_batches()` abre una conexión y un cursor, ejecuta una sola vez y retorna `SqlBatchStream`. El
stream solicita `batch_size` filas por `fetchmany()`, convierte sólo ese fragmento a tuplas y lo
entrega. Agotarlo o cerrar su context manager libera cursor y conexión.

Si el cuerpo de un `with` falla y el cierre también falla, se preserva el error primario. Un fallo
de cierre sin error previo se normaliza como `SqlConnectionError`.

## Conexión y transacción

Cada llamada `query()` y cada stream usan una conexión propia. Se abre con `autocommit=True` y
`native_uuid=False`. El primer valor evita mantener una transacción de lectura durante todo un
stream; el segundo conserva `UNIQUEIDENTIFIER` como `str` para no cambiar el contrato que tenían los
consumidores con PyODBC.

La superficie pública no ofrece escritura ni control de transacciones y tampoco interpreta texto
SQL. La garantía efectiva de sólo lectura proviene de permisos del usuario SQL.

## Timeouts y fallos

El secreto puede declarar `Connection Timeout`, pero `mssql-python` no lo admite como keyword de
connection string. La frontera de compatibilidad lo traduce a `connect(timeout=...)`.
`query_timeout_seconds` sigue siendo independiente: se aplica a la conexión y se hereda por los
cursores. Los estados `HYT00` y `HYT01`, junto con los diagnósticos de
timeout expuestos por `mssql-python`, se normalizan como `SqlTimeoutError`, distinguiendo fase
`connect` o `query`.

Cada operación realiza exactamente un intento. Una capa que conozca la fuente decide si una consulta
es idempotente y reintentable.

Las excepciones originales del driver no se encadenan porque pueden contener servidor, base, SQL o
credenciales.

## Runtime y warning de Python 3.14

`mssql-python` se carga al abrir una conexión, no al importar el paquete. La versión 1.13.0 emite en
Python 3.14 un `SyntaxWarning` conocido por un `return` dentro de `finally`. Atlanticus lo filtra sólo
durante el import lazy, por categoría y mensaje exactos. Otros warnings permanecen visibles.

El cliente productivo usa `execute()` y `fetchmany()`; no depende de `arrow_reader()` ni de la ruta
Arrow afectada por ese warning upstream.

## Aceptación

El runner `python:3.14.2-slim-bookworm` instala sólo las librerías Linux requeridas por
`mssql-python`; no instala UnixODBC ni paquetes `msodbcsql`. El contenedor de prueba queda
multi-arquitectura. SQL Server 2019 permanece `linux/amd64` porque esa imagen es la restricción del
servidor, no del cliente Atlanticus.

La batería valida:

- connection string nativa y compatibilidad legacy Driver 17/18;
- health check y parámetros posicionales;
- enteros, Unicode, Decimal, `datetime`, booleanos, bytes y nulos;
- `UNIQUEIDENTIFIER` como `str`;
- resultado vacío y límite de `query()`;
- lotes `7 + 7 + 7 + 4` sobre 25 filas;
- timeout real;
- autenticación incorrecta y error SQL sanitizados;
- escritura denegada por permisos;
- tres lecturas concurrentes con conexiones independientes;
- instalación del wheel construido.
