# atlanticus-kernel

`atlanticus-kernel` es la base mínima de los módulos Python de Atlanticus. No contiene lógica de
negocio ni integra servicios externos.

## Qué hace

| Capacidad | Clase o función | Resultado |
|---|---|---|
| Ambiente | `Environment` | Valida `ENVIRONMENT` y expone propiedades simples |
| Nombre de ambiente | `EnvironmentName` | Conjunto cerrado de ambientes admitidos |
| Estado | `OperationStatus` | Estado general y serializable de una operación |
| Tiempo | `utc_now()` | `datetime` consciente de zona horaria en UTC |
| Sanitización | `DataSanitizer` | Convierte valores a estructuras seguras para JSON |
| Errores | `KernelError` | Base de errores propios del wheel |

## Qué no hace

- No carga archivos `.env`.
- No obtiene secretos.
- No configura logs.
- No crea clientes cloud.
- No ejecuta jobs.
- No conoce Flask, Dash o Gunicorn.
- No conoce ADA ni una identidad de aplicación.

## Versiones

| Elemento | Versión |
|---|---:|
| Python objetivo | `3.14.2` |
| Wheel | `0.1.0` |
| Setuptools para build | `83.0.0` |
| pytest para desarrollo | `9.1.1` |
| Ruff para desarrollo | `0.15.22` |

El wheel no tiene dependencias productivas fuera de la biblioteca estándar.

## Instalación

Desde el workspace:

```bash
uv sync --locked --all-packages --group dev
```

Desde un wheel ya construido:

```bash
uv pip install dist/atlanticus_kernel-0.1.0-py3-none-any.whl
```

## Ambiente

Valores admitidos:

| Valor | Significado |
|---|---|
| `local` | Computador local de cualquier desarrollador |
| `dev` | Desarrollo desplegado |
| `uat` | Validación de usuario |
| `stg` | Etapa de transición o preproducción equivalente a UAT |
| `prd` | Producción |

El contrato compara el texto de forma exacta. No convierte mayúsculas, no elimina espacios y no
traduce alias:

```text
uat  -> válido
stg  -> válido y se conserva como stg
UAT  -> inválido
prod -> inválido
stage -> inválido
```

Aunque `uat` y `stg` cumplen temporalmente un propósito operacional equivalente, permanecen como
valores distintos. El kernel nunca convierte uno en el otro porque el nombre exacto puede utilizarse
para resolver recursos de infraestructura, como Key Vault.

Uso:

```python
from atlanticus.kernel import Environment

environment = Environment.from_os()

if environment.is_local:
    print('Detalles locales habilitados')

if environment.is_production:
    print('Ejecución productiva')
```

También puede leerse desde un mapping explícito, lo que facilita las pruebas:

```python
environment = Environment.from_mapping({'ENVIRONMENT': 'uat'})
```

Si `ENVIRONMENT` no existe, está vacío o contiene cualquier valor distinto de los cinco oficiales,
se produce `InvalidEnvironmentError`. Para trabajar en un computador personal se debe configurar
explícitamente `ENVIRONMENT=local`.

## Tiempo UTC

```python
from atlanticus.kernel import utc_now

started_at = utc_now()
```

La función retorna un `datetime` con `tzinfo=UTC`; nunca retorna una fecha naive.

## Estados

```python
from atlanticus.kernel import OperationStatus

status = OperationStatus.SUCCESS
assert str(status) == 'success'
```

Estados disponibles:

```text
success
warning
error
skipped
```

## Sanitización

```python
from atlanticus.kernel import DataSanitizer

sanitizer = DataSanitizer()

safe_payload = sanitizer.sanitize(
    {
        'user': 'demo',
        'access_token': 'secret-value',
        'items': list(range(100)),
    }
)
```

La sanitización:

- enmascara campos cuyos nombres parezcan sensibles;
- convierte fechas, rutas, UUID y enumeraciones;
- resume bytes y excepciones sin conservar mensajes de error;
- limita profundidad, cantidad de elementos y largo de textos;
- no ejecuta `repr` de objetos desconocidos;
- transforma `NaN` e infinitos a texto válido para JSON.

Se pueden ajustar límites por instancia:

```python
sanitizer = DataSanitizer(
    max_depth=3,
    max_items=20,
    max_string_length=250,
)
```

No debe utilizarse para cifrar ni proteger datos almacenados. Es una protección preventiva para
payloads operacionales, no una solución de seguridad completa.

## Errores esperados

### `InvalidEnvironmentError`

Causas:

```text
ENVIRONMENT=
ENVIRONMENT=UAT
ENVIRONMENT=prod
ENVIRONMENT=production-east
```

Solución: utilizar exactamente `local`, `dev`, `uat`, `stg` o `prd`. La región, aplicación o mina
deben configurarse en variables diferentes; no forman parte de `ENVIRONMENT`.

### `ValueError` al construir `DataSanitizer`

Causa: uno de sus límites es negativo o cero.

Solución:

```python
DataSanitizer(max_depth=4, max_items=50, max_string_length=500)
```

### `ModuleNotFoundError: atlanticus`

Causa: el wheel no está instalado o el ambiente virtual no está activo.

Solución:

```bash
uv sync --locked --all-packages --group dev
```

### Versión de Python incompatible

Causa: el proyecto exige Python `3.14.2` de forma exacta.

Solución:

```bash
uv python install 3.14.2
uv sync --locked --all-packages --group dev
```

## Pruebas

Desde la raíz de `atlanticus/backend`, una prueba individual puede ejecutarse con:

```bash
uv run pytest kernel/tests/test_environment.py
```

## Construcción del wheel

```bash
uv build --package atlanticus-kernel --out-dir dist --clear
```

Resultado esperado:

```text
dist/atlanticus_kernel-0.1.0-py3-none-any.whl
```

## Integración en otro módulo

El consumidor declara una dependencia exacta:

```toml
[project]
dependencies = [
    "atlanticus-kernel==0.1.0",
]
```

El espejo comentado se encuentra en `commented/atlanticus/kernel/`. Contiene los mismos
archivos e instrucciones del código productivo y agrega solamente comentarios explicativos. La
prueba `tests/test_commented_mirror.py` impide que ambas rutas diverjan.

El diseño se documenta en `docs/design.md`.
