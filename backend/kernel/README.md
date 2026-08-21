<p align="right">
  <img src="../../docs/assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Atlanticus Kernel

[Volver a Backend](../README.md) · [Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)

`atlanticus-kernel` contiene las primitivas más pequeñas y estables que pueden utilizar las demás
librerías y aplicaciones Python de Atlanticus. No implementa infraestructura, observabilidad,
runtime ni reglas funcionales.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.0.1` |
| Estado | Validado |
| Ruta física | `backend/kernel/` |
| Distribución | `atlanticus-kernel` |
| Import público | `atlanticus.kernel` |
| Versión técnica actual | `0.1.0` |
| Python requerido | `3.14.2` |
| Dependencias productivas | Ninguna fuera de la biblioteca estándar |
| Tipo | Wheel tipado y reutilizable |

La versión técnica se obtiene de `[project].version` en `backend/kernel/pyproject.toml`. No debe
actualizarse modificando únicamente este README.

## Propósito

Kernel responde a necesidades comunes que siguen teniendo sentido aunque se retiren Azure, Flask,
Dash, Cosmos, Redis, Pandas y todos los conceptos de ADA.

Incluye:

- lectura y validación estricta del ambiente de ejecución;
- nombres oficiales de ambiente;
- estado genérico y serializable de una operación;
- obtención de fecha y hora UTC consciente de zona horaria;
- sanitización defensiva de estructuras destinadas a diagnóstico;
- errores base propios del módulo.

Es una librería, no una aplicación. No declara un entrypoint ni se ejecuta como proceso.

## Límites

Kernel deliberadamente:

- no carga archivos `.env`;
- no resuelve secretos;
- no configura logs ni observabilidad;
- no crea clientes cloud;
- no lee ni escribe state o datasets;
- no ejecuta jobs;
- no conoce aplicaciones, navegación, usuarios ni autorización;
- no conoce PI, Dispatch, KPI, alarmas ni ADA;
- no depende de otros wheels de Atlanticus.

Si una capacidad necesita infraestructura, estado, ciclo de vida o conocimiento funcional,
pertenece a otro módulo. Mantener esta frontera permite consumir Kernel sin arrastrar dependencias
innecesarias.

## API pública

Solo los símbolos exportados por `atlanticus.kernel.__init__` forman parte del contrato público.

| Símbolo | Tipo | Responsabilidad |
|---|---|---|
| `ENVIRONMENT_VARIABLE` | Constante | Nombre exacto de la variable `ENVIRONMENT`. |
| `EnvironmentName` | `StrEnum` | Catálogo cerrado de ambientes admitidos. |
| `Environment` | Dataclass inmutable | Ambiente ya validado y consultable. |
| `KernelError` | Excepción base | Raíz de errores propios de Kernel. |
| `InvalidEnvironmentError` | Excepción | Detalle estructurado de un ambiente inválido. |
| `OperationStatus` | `StrEnum` | Resultado general de una operación. |
| `utc_now()` | Función | Fecha y hora UTC timezone-aware. |
| `DataSanitizer` | Clase | Conversión defensiva a estructuras compatibles con JSON. |
| `REDACTED` | Constante | Marcador `***redacted***` aplicado a campos sensibles. |
| `__version__` | Texto | Versión expuesta en runtime. |

Los módulos internos pueden cambiar sin considerarse API pública mientras no alteren estos
contratos o su comportamiento observable.

## 1. Ambiente de ejecución

Kernel conoce exclusivamente:

```dotenv
ENVIRONMENT=<valor>
```

No lee `ATLANTICUS_ENVIRONMENT`, `APP_NAME`, `APPLICATION`, región, faena ni identificadores de
infraestructura. Esos datos pertenecen a otros contratos.

### Valores admitidos

| Valor | Representación | Significado técnico |
|---|---|---|
| `local` | `EnvironmentName.LOCAL` | Ejecución explícitamente local. |
| `dev` | `EnvironmentName.DEV` | Ambiente desplegado de desarrollo. |
| `uat` | `EnvironmentName.UAT` | Validación de usuario. |
| `stg` | `EnvironmentName.STG` | Etapa o preproducción diferenciada. |
| `prd` | `EnvironmentName.PRD` | Producción. |

La comparación es exacta. No se eliminan espacios, no se convierten mayúsculas y no existen alias:

```text
'uat'  -> válido
'stg'  -> válido y se conserva como stg
'UAT'  -> inválido
' uat' -> inválido
'uat ' -> inválido
'prod' -> inválido
'stage' -> inválido
```

`uat` y `stg` permanecen como valores distintos aunque una organización los utilice temporalmente
con propósitos similares. El texto puede participar en la resolución de recursos externos y Kernel
no debe traducirlo.

### Construcción desde un valor

```python
from atlanticus.kernel import Environment

environment = Environment.from_value('uat')

assert str(environment) == 'uat'
assert environment.is_uat
```

`from_value()` también acepta una instancia de `EnvironmentName`. El constructor directo exige que
`name` ya sea de ese tipo; entregar un `str` directamente produce `InvalidEnvironmentError`.

### Lectura desde un mapping

```python
environment = Environment.from_mapping({'ENVIRONMENT': 'dev'})
```

El método solo consulta la clave declarada en `ENVIRONMENT_VARIABLE`. Otros valores del mapping se
ignoran.

### Lectura desde el proceso

```python
environment = Environment.from_os()
```

`from_os()` lee `os.environ`. Si la variable falta, está vacía o contiene un valor no oficial, el
proceso falla explícitamente. No existe fallback automático a `local`; esta decisión evita que un
deployment incompleto omita accidentalmente infraestructura obligatoria.

### Propiedades disponibles

| Propiedad | Condición verdadera |
|---|---|
| `is_local` | `name is EnvironmentName.LOCAL` |
| `is_uat` | `name is EnvironmentName.UAT` |
| `is_stg` | `name is EnvironmentName.STG` |
| `is_production` | `name is EnvironmentName.PRD` |

No existe actualmente `is_dev`. Cuando sea necesario, el consumidor compara
`environment.name is EnvironmentName.DEV`.

## 2. Estado general de una operación

`OperationStatus` representa un resultado técnico pequeño y serializable:

```python
from atlanticus.kernel import OperationStatus

status = OperationStatus.SUCCESS
assert str(status) == 'success'
```

| Miembro | Valor serializado |
|---|---|
| `SUCCESS` | `success` |
| `WARNING` | `warning` |
| `ERROR` | `error` |
| `SKIPPED` | `skipped` |

No debe utilizarse para sustituir estados específicos de un dominio. Por ejemplo, la semántica de
un KPI, una alarma o una publicación de dataset pertenece a su contrato propietario.

## 3. Tiempo UTC

```python
from atlanticus.kernel import utc_now

started_at = utc_now()
```

`utc_now()` retorna un `datetime` con `tzinfo` igual a `datetime.UTC`. Nunca produce una fecha
naive y no acepta parámetros.

Kernel no calcula calendarios operacionales, turnos, ventanas, zonas locales ni scheduling. Solo
proporciona el instante UTC actual.

## 4. Sanitización defensiva

`DataSanitizer` transforma valores en estructuras acotadas que pueden entregarse a JSON, logs o
eventos sin ejecutar representaciones arbitrarias de objetos.

```python
from atlanticus.kernel import DataSanitizer

sanitizer = DataSanitizer()
safe_payload = sanitizer.sanitize(
    {
        'user': 'demo',
        'access_token': 'sensitive-value',
        'items': list(range(100)),
    }
)
```

El resultado:

- reemplaza valores de claves sensibles por `***redacted***`;
- convierte fechas y horas mediante `isoformat()`;
- convierte `timedelta` a segundos;
- convierte `Decimal`, `UUID` y `Path` a texto;
- utiliza el valor de una enumeración;
- resume bytes indicando tipo y tamaño;
- resume una excepción únicamente por su tipo;
- limita profundidad, cantidad de elementos y longitud de textos;
- convierte `NaN`, `Infinity` y `-Infinity` a textos válidos para JSON;
- no llama `repr()` ni `str()` sobre objetos desconocidos.

### Límites predeterminados

| Parámetro | Default | Regla |
|---|---:|---|
| `max_depth` | `4` | Debe ser mayor o igual a cero. |
| `max_items` | `50` | Debe ser mayor que cero. |
| `max_string_length` | `500` | Debe ser mayor que cero. |
| `sensitive_key_parts` | Catálogo interno | Debe contener textos no vacíos. |

Los límites pueden ajustarse por instancia:

```python
sanitizer = DataSanitizer(
    max_depth=3,
    max_items=20,
    max_string_length=250,
)
```

Cuando se supera la cantidad de elementos, el resultado incorpora un marcador de truncamiento.
Cuando se alcanza el límite de profundidad para una estructura, se entrega su tipo y
`max_depth_reached`.

### Detección de claves sensibles

El nombre de la clave se normaliza ignorando mayúsculas y caracteres no alfanuméricos. Por ejemplo,
`api-key`, `ApiKey` y `api_key` se comparan de forma equivalente.

El catálogo cubre familias como password, secret, token, credentials, connection strings, access
keys, API keys, account keys, authorization, SAS y shared access signatures. Puede reemplazarse con
un catálogo propio al construir la instancia.

Si una clave se considera sensible, se oculta su valor completo antes de inspeccionar su contenido.
Esto evita recorrer estructuras que ya fueron clasificadas como secretas.

### Límites de seguridad

La sanitización es una defensa adicional, no cifrado ni garantía de ausencia de secretos.

- No inspecciona el contenido de un texto bajo una clave no sensible.
- Un mensaje construido por el consumidor podría contener información confidencial.
- Un catálogo personalizado incompleto puede dejar campos sin detectar.
- Una coincidencia conservadora puede ocultar información que no era secreta.
- El resultado sigue necesitando una política correcta de logs, acceso y retención.

El productor de cada evento continúa siendo responsable de no incluir credenciales, consultas,
filas completas ni URLs firmadas.

## 5. Errores

Todos los errores propios heredan de `KernelError`.

### `InvalidEnvironmentError`

Expone:

| Atributo | Contenido |
|---|---|
| `value` | Valor rechazado, incluido `None`. |
| `allowed_values` | Tupla ordenada de valores oficiales. |

El mensaje conserva el contrato técnico en inglés:

```text
Invalid environment 'production-east'. Allowed values: local, dev, uat, stg, prd.
```

### Errores de construcción de `DataSanitizer`

Una configuración inválida produce `ValueError` durante el constructor. No se corrige ni se
normaliza silenciosamente.

Ejemplos de mensajes:

```text
max_depth must be greater than or equal to zero.
max_items must be greater than zero.
max_string_length must be greater than zero.
sensitive_key_parts must not be empty.
```

## Dependencias y consumidores

El wheel no tiene dependencias productivas. Sus consumidores internos declaran un pin exacto y una
fuente local editable durante desarrollo.

Kernel es consumido directamente por:

- Configuration y Observability dentro de Backend;
- Key Vault y el workspace Connectivity;
- Integrations;
- Data Producers;
- capacidades KPI;
- procesos backend de ADA.

No todos los consumidores necesitan declararlo directamente. Si solo utilizan un contrato que ya
lo encapsula, deben depender del módulo propietario y evitar pins redundantes.

## Source y mirror pedagógico

```text
backend/kernel/
├── pyproject.toml
├── src/atlanticus/kernel/
│   ├── __init__.py
│   ├── environment.py
│   ├── errors.py
│   ├── sanitization.py
│   ├── status.py
│   ├── time.py
│   └── py.typed
├── commented/atlanticus/kernel/
├── tests/
└── docs/design.md
```

El código productivo se encuentra en `src/`. El directorio `commented/` conserva un espejo con
comentarios pedagógicos en español y no se distribuye en el wheel.

`tests/test_commented_mirror.py` compara los archivos y sus tokens de Python ignorando comentarios
y líneas no significativas. Un cambio funcional debe aplicarse de forma equivalente en ambas
rutas.

## Pruebas y validación

El snapshot contiene 28 pruebas distribuidas en:

| Archivo | Contrato validado |
|---|---|
| `test_environment.py` | Valores oficiales, errores, mappings y propiedades. |
| `test_sanitization.py` | Redacción, tipos, límites, JSON seguro y objetos desconocidos. |
| `test_status_and_time.py` | Estados, UTC y versión pública. |
| `test_commented_mirror.py` | Archivos y equivalencia source/mirror. |

El gate oficial del módulo es `backend/scripts/validation/check.sh kernel --clean`, ejecutado desde
la raíz del repositorio según las guías transversales. Aplica Ruff y formato antes de comprobarlos,
por lo que puede modificar el código y el mirror.

El gate además ejecuta las pruebas, comprueba `import atlanticus.kernel`, construye un único wheel
y valida su presencia en `backend/dist/`.

| Necesidad | Documento propietario |
|---|---|
| Preparar el workspace y ejecutar pruebas | [Primeros pasos y desarrollo](../../docs/development.md) |
| Construir e inspeccionar el wheel | [Empaquetado](../../docs/packaging.md) |
| Actualizar versión y consumidores | [Versionamiento](../../docs/versioning.md) |

## Construcción y contenido del wheel

El resultado sigue el patrón:

```text
backend/dist/atlanticus_kernel-<version>-py3-none-any.whl
```

El wheel debe incluir `atlanticus/kernel/*.py` y `py.typed`. No debe incluir `tests/`, `commented/`,
`docs/`, caches ni metadata de desarrollo.

Kernel utiliza `setuptools.build_meta`. La versión del backend de build y las herramientas de
desarrollo se consultan en sus `pyproject.toml` propietarios y en `backend/uv.lock`; no forman parte
del API runtime de Kernel.

## Actualización de versión

Antes de publicar una revisión deben evaluarse como contrato público:

- valores y semántica de `EnvironmentName`;
- nombre y comportamiento de `ENVIRONMENT`;
- propiedades y constructores de `Environment`;
- miembros de `OperationStatus`;
- firma y timezone de `utc_now()`;
- tipos, límites y reglas de `DataSanitizer`;
- jerarquía, atributos y mensajes de errores;
- exports de `__init__.py` y comportamiento de `__version__`.

Una actualización debe mantener alineados:

1. `[project].version` en `pyproject.toml`;
2. `__version__` en source y mirror;
3. prueba de versión pública;
4. pins exactos de consumidores;
5. `backend/uv.lock`;
6. README del módulo;
7. wheels y artifacts derivados.

Ruff, formato y pruebas se ejecutan antes y después del cambio de versión conforme a
[Versionamiento](../../docs/versioning.md). No se reutiliza una versión ya entregada para contenido
diferente.

## Elementos no verificados o pendientes

- No se ejecutó el gate de Kernel durante esta modificación exclusivamente documental.
- No se verificó instalación desde el wheel en un ambiente externo al workspace.
- Los consumidores listados se identificaron desde los `pyproject.toml` del snapshot; pueden
  existir consumidores fuera del repositorio.
- El uso futuro de otro nombre de variable de ambiente requiere una migración coordinada; Kernel
  actualmente reconoce únicamente `ENVIRONMENT`.
- `docs/design.md` conserva referencias históricas a etapas iniciales y deberá revisarse por
  separado si se incorpora al índice documental validado.

## Control documental

La versión `1.0.1` corresponde únicamente a este README. La versión técnica actual del wheel
continúa siendo la declarada por su `pyproject.toml`.

El documento se encuentra **Validado**. Su aprobación no modifica código ni publica una nueva
versión de Kernel.

---

[Volver a Backend](../README.md) · [Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)
