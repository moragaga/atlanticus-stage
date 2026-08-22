<p align="right">
  <img src="../../docs/assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Atlanticus Configuration

[Volver a Backend](../README.md) · [Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)

`atlanticus-configuration` proporciona el bootstrap fail-fast que utilizan los procesos backend de
Atlanticus para declarar, resolver y consultar su configuración. Separa el contrato de variables
de las fuentes físicas y entrega un resultado completo e inmutable antes de iniciar el trabajo
funcional.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.0.1` |
| Estado | Validado |
| Ruta física | `backend/configuration/` |
| Distribución | `atlanticus-configuration` |
| Import público | `atlanticus.configuration` |
| Versión técnica actual | `0.1.0` |
| Python requerido | `3.14.2` |
| Dependencias productivas | `atlanticus-kernel==0.1.0`, `python-dotenv==1.2.2` |
| Tipo | Wheel tipado y reutilizable |

La versión técnica se obtiene de `[project].version` en
`backend/configuration/pyproject.toml`. No debe actualizarse modificando únicamente este README.

## Propósito

El módulo define cuatro responsabilidades relacionadas:

1. declarar las variables que una aplicación permite resolver;
2. seleccionar las fuentes según el ambiente backend;
3. resolver todos los valores requeridos o fallar sin resultado parcial;
4. conservar la procedencia y sensibilidad de cada valor resuelto.

Configuration es una librería. No declara entrypoints, no inicia procesos y no representa por sí
misma una aplicación ejecutable.

## Límites

Atlanticus Configuration deliberadamente:

- no define las variables funcionales de un proceso concreto;
- no conoce reglas de ADA, KPI, PI, alarmas ni otros dominios;
- no interpreta `config.json` ni `services.json`;
- no crea rutas de datasets, logs, state o volúmenes;
- no autentica contra Azure;
- no crea ni administra clientes de Key Vault;
- no deriva el nombre del vault;
- no convierte automáticamente URLs, duraciones, rutas o modelos de dominio;
- no modifica `os.environ` al leer `.env`;
- no interpola referencias `${VARIABLE}` dentro de dotenv;
- no registra ni persiste la configuración resuelta.

La integración con Azure Key Vault se realiza mediante el puerto `SecretResolver`. Su
implementación concreta pertenece a `connectivity/key-vault`, manteniendo este wheel independiente
del SDK de Azure.

## Posición arquitectónica

Configuration depende de Kernel para reutilizar el contrato de ambiente:

```text
atlanticus-kernel ← atlanticus-configuration ← procesos backend
```

Los procesos son propietarios de sus `ConfigurationVariableSpec`. El módulo transversal resuelve
esas especificaciones, pero no decide qué significa cada valor para el consumidor.

## API pública

Solo los catorce símbolos exportados por `atlanticus.configuration.__init__` forman parte del
contrato público.

| Símbolo | Tipo | Responsabilidad |
|---|---|---|
| `ConfigurationBootstrap` | Dataclass inmutable | Selecciona fuentes y resuelve el contrato completo. |
| `ConfigurationVariableSpec` | Dataclass inmutable | Declara una variable esperada por la aplicación. |
| `ResolvedConfiguration` | Dataclass inmutable | Expone valores, fuentes y sensibilidad ya resueltos. |
| `ConfigurationSource` | `StrEnum` | Identifica `process`, `dotenv`, `manifest`, `key_vault` o `default`. |
| `SecretResolver` | `Protocol` | Puerto mínimo para obtener un secreto por nombre. |
| `SecretManifestEntry` | Dataclass inmutable | Representa una entrada validada de `secrets.json`. |
| `SecretsManifest` | Dataclass inmutable | Lee, valida e indexa el manifiesto completo. |
| `ConfigurationError` | Excepción base | Raíz de los errores del módulo. |
| `ConfigurationSourceError` | Excepción | Fuente ausente, inválida o incompatible con el ambiente. |
| `ConfigurationValueError` | Excepción | Nombre, tipo o conversión de valor inválida. |
| `MissingConfigurationVariablesError` | Excepción | Variables obligatorias no resueltas. |
| `SecretResolutionError` | Excepción | Fallo sanitizado al resolver un secreto. |
| `SecretsManifestError` | Excepción | Manifiesto ausente, ilegible o estructuralmente inválido. |
| `__version__` | Texto | Versión expuesta en runtime. |

Las funciones internas de normalización y validación no se exportan desde el package raíz y no
deben tratarse como API estable.

## 1. Declarar variables

Cada aplicación construye una secuencia de especificaciones:

```python
from atlanticus.configuration import ConfigurationVariableSpec

specs = (
    ConfigurationVariableSpec(key='APPLICATION'),
    ConfigurationVariableSpec(key='VOLUMEN_PATH'),
    ConfigurationVariableSpec(key='SERVICE_TOKEN', sensitive=True),
    ConfigurationVariableSpec(key='POLL_INTERVAL_SECONDS', default='10'),
    ConfigurationVariableSpec(key='OPTIONAL_LABEL', required=False),
)
```

Los nombres anteriores son ilustrativos. El catálogo real pertenece a cada proceso.

| Campo | Default | Contrato |
|---|---:|---|
| `key` | Obligatorio | Mayúsculas, números y guiones bajos; debe comenzar con una letra. |
| `required` | `True` | Indica si la ausencia debe detener el bootstrap. |
| `default` | `None` | Fallback textual aplicado después de consultar la fuente del ambiente. |
| `sensitive` | `False` | Enmascara el valor en exportaciones diagnósticas. |

Una variable sensible no puede declarar default. Las claves repetidas se rechazan al construir el
bootstrap. `ENVIRONMENT` está reservada por Kernel y no puede formar parte de `specs`.

Configuration resuelve solo las variables declaradas. Una variable presente en `.env`, en el
ambiente del proceso o en `secrets.json` no aparece en el resultado si no existe un spec
correspondiente.

## 2. Seleccionar el ambiente

`ConfigurationBootstrap.from_process()` resuelve primero `ENVIRONMENT`. Los valores admitidos por
el contrato backend son:

| Valor | Clasificación |
|---|---|
| `local` | Ejecución local. |
| `dev` | Ambiente desplegado de desarrollo. |
| `uat` | Pruebas de aceptación. |
| `stg` | Staging. |
| `prd` | Producción. |

No se aceptan alias, mayúsculas ni espacios adicionales.

La variable puede proceder del mapping del proceso en cualquier ambiente. Solo `local` puede
seleccionarse desde `.env`; un valor desplegado encontrado únicamente en dotenv se rechaza para
impedir que un archivo local decida accidentalmente la fuente productiva.

El ambiente se comprueba nuevamente al llamar a `load()`. Si cambió entre la creación del
bootstrap y la resolución, el módulo falla antes de solicitar secretos.

Este contrato pertenece a los procesos backend del snapshot y utiliza literalmente `ENVIRONMENT`.
No debe asumirse equivalente a selectores de la capa web sin revisar el contrato propietario de
esa capa.

## 3. Resolución local

En `local`, las fuentes efectivas siguen esta precedencia:

```text
mapping del proceso > archivo .env > default del spec
```

```python
from atlanticus.configuration import ConfigurationBootstrap

bootstrap = ConfigurationBootstrap.from_process(
    specs=specs,
    dotenv_path='.env',
)
configuration = bootstrap.load()
```

Si no se entrega `process_values`, el módulo consulta `os.environ`, pero nunca lo modifica. El
archivo `.env` es opcional cuando el mapping del proceso contiene `ENVIRONMENT=local` y todos los
valores obligatorios.

La precedencia conserva incluso los valores vacíos. Si el proceso define `TOKEN=` y dotenv contiene
un valor válido, la fuente superior no permite recuperar silenciosamente el valor inferior. El
valor se considera ausente y se aplica un default permitido o se informa el error.

La lectura utiliza `python-dotenv` con interpolación deshabilitada:

```dotenv
URL=https://${DOMAIN}/api
```

El resultado anterior permanece literalmente como `https://${DOMAIN}/api`.

## 4. Resolución desplegada

En `dev`, `uat`, `stg` y `prd`, `load()` exige un `SecretsManifest`. Para cada spec:

1. busca una entrada con el mismo `var_name`;
2. usa `value` cuando `exists_in_key_vault` es `false`;
3. llama a `SecretResolver.get_secret(secret_name)` cuando es `true`;
4. aplica el default del spec cuando no existe valor activo;
5. informa conjuntamente las variables obligatorias ausentes.

El mapping del proceso se utiliza para verificar `ENVIRONMENT`, pero no reemplaza valores del
manifiesto en un ambiente desplegado. `.env` tampoco participa en esa resolución.

Antes de consultar un secreto, el preflight comprueba que todas las variables obligatorias sin
default estén representadas en el manifiesto. Si falta alguna, no se realizan solicitudes
parciales al resolver.

## 5. Contrato de `secrets.json`

`SecretsManifest.from_path()` exige una raíz JSON array. Cada entrada debe contener exactamente:

```json
{
  "var_name": "SERVICE_CONNECTION_STRING",
  "secret_name": "secret-service-connection-string",
  "value": null,
  "exists_in_key_vault": true
}
```

| Campo | Responsabilidad |
|---|---|
| `var_name` | Variable declarada por la aplicación. |
| `secret_name` | Nombre solicitado al resolver o `null`. |
| `value` | Valor estático o `null`. |
| `exists_in_key_vault` | Selector booleano y autoritativo de la fuente. |

Cuando `exists_in_key_vault=true`, `secret_name` debe ser texto no vacío. Cuando es `false`,
`value` debe ser un string distinto de `""`. El campo inactivo no actúa como fallback aunque
contenga información.

El manifiesto rechaza:

- una raíz que no sea array;
- elementos que no sean objetos;
- campos obligatorios ausentes;
- campos desconocidos;
- variables duplicadas;
- nombres de variable inválidos;
- una entrada para `ENVIRONMENT`;
- tipos diferentes de los declarados;
- una fuente activa sin nombre o valor utilizable.

Los valores estáticos conservan espacios, incluso un string formado solo por espacios. El módulo
también conserva el texto exacto de un secreto resuelto.

Configuration solo comprueba que `secret_name` sea texto no vacío. La longitud, caracteres
permitidos y existencia real del secreto deben validarse en la implementación de
`SecretResolver`; `KeyVaultClient` aplica las restricciones de Azure al realizar la consulta.

`SecretsManifest.find()` busca una entrada por variable. `static_values()` devuelve una vista
inmutable formada únicamente por los valores cuya fuente activa es el propio manifiesto.

## 6. Resultado inmutable

`load()` retorna `ResolvedConfiguration` únicamente cuando puede construir el contrato completo:

```python
application = configuration.require('APPLICATION')
optional_label = configuration.get('OPTIONAL_LABEL')
enabled = configuration.get_bool('FEATURE_ENABLED', default=False)
workers = configuration.get_int('WORKERS', default=1)
```

El resultado contiene mappings de solo lectura para `values` y `sources`, además de un
`frozenset` con las claves sensibles.

| Método | Comportamiento |
|---|---|
| `get()` | Obtiene un valor opcional. |
| `require()` | Exige un valor presente y no vacío. |
| `get_bool()` | Acepta `1`, `true`, `yes`, `on`, `0`, `false`, `no`, `off`. |
| `get_int()` | Convierte mediante `int()` y asocia el error a la variable. |
| `to_dict()` | Devuelve una copia y enmascara sensibles con `***`. |

Los valores permanecen como strings hasta que el consumidor solicita una conversión. Configuration
no conoce rangos, formatos de URL, paths absolutos, cron, duraciones ni semántica funcional; esas
validaciones pertenecen al módulo propietario.

## Valores sensibles

Una clave se considera sensible cuando:

- su spec declara `sensitive=True`; o
- su valor procede de `SecretResolver`.

`repr(configuration)` muestra ambiente y nombres de claves, pero no valores. `to_dict()` enmascara
por defecto. La variante `to_dict(mask_sensitive=False)` expone una copia completa y debe
reservarse para composición controlada; nunca debe enviarse a logs, telemetría o mensajes de
error.

Los errores del resolver se transforman en `SecretResolutionError` y descartan el detalle de la
excepción original. El mensaje identifica la variable funcional, no el valor secreto ni la
credencial utilizada.

## Jerarquía de errores

| Error | También hereda de | Uso |
|---|---|---|
| `ConfigurationError` | `RuntimeError` | Captura general del módulo. |
| `ConfigurationSourceError` | `ConfigurationError` | Fuente ausente o ambiente inconsistente. |
| `SecretsManifestError` | `ConfigurationError`, `ValueError` | Archivo o estructura del manifiesto inválida. |
| `ConfigurationValueError` | `ConfigurationError`, `ValueError` | Nombre, tipo o conversión inválida. |
| `MissingConfigurationVariablesError` | `ConfigurationError` | Variables requeridas ausentes. |
| `SecretResolutionError` | `ConfigurationError` | Resolución segura de secreto fallida. |

Los mensajes técnicos permanecen en inglés. Las variables faltantes se ordenan para producir un
resultado determinístico.

## Dependencias y consumidores

Las dependencias productivas directas son:

| Distribución | Uso |
|---|---|
| `atlanticus-kernel` | `Environment`, `ENVIRONMENT_VARIABLE` y error de ambiente. |
| `python-dotenv` | Lectura local de `.env` sin interpolación. |

El SDK de Azure y `atlanticus-key-vault` no son dependencias de este wheel. El proceso concreto
inyecta un objeto compatible con `SecretResolver` cuando necesita secretos externos.

Los nueve procesos ADA del snapshot declaran directamente `atlanticus-configuration==0.1.0`:

- `pi-web-api`;
- `notpii`;
- `dispatch`;
- `blockgrade`;
- `fabrica`;
- `remanentes`;
- `kpis`;
- `kpis-historian`;
- `kpis-delivery`.

Configuration no importa ninguno de esos procesos ni conoce sus catálogos.

## Source y mirror pedagógico

```text
backend/configuration/
├── pyproject.toml
├── src/atlanticus/configuration/
│   ├── __init__.py
│   ├── bootstrap.py
│   ├── contracts.py
│   ├── errors.py
│   ├── manifest.py
│   ├── models.py
│   └── py.typed
├── commented/atlanticus/configuration/
└── tests/
```

El código productivo vive en `src/`. `commented/` conserva el espejo pedagógico en español y queda
fuera del wheel. Su prueba de paridad exige los mismos archivos Python y los mismos tokens no
comentarios que la implementación productiva.

## Pruebas y validación

El snapshot contiene 45 funciones de prueba que Pytest expande a 58 casos por parametrización:

| Archivo | Contrato validado |
|---|---|
| `test_bootstrap_local.py` | Precedencia, dotenv, ambiente y errores locales. |
| `test_bootstrap_deployed.py` | Manifiesto, resolver, preflight y sanitización. |
| `test_manifest.py` | Schema JSON, duplicados, campos y valores estáticos. |
| `test_models.py` | Inmutabilidad, conversiones, defaults y valores sensibles. |
| `test_configuration_commented_mirror.py` | Archivos y equivalencia source/mirror. |
| `test_public_api_and_boundaries.py` | Exports, versión y límites arquitectónicos. |

El gate oficial es `backend/scripts/validation/check.sh configuration --clean`. Aplica Ruff y
formato antes de comprobarlos, ejecuta pruebas, valida `import atlanticus.configuration`, construye
el wheel y verifica su presencia en `backend/dist/`.

| Necesidad | Documento propietario |
|---|---|
| Preparar Python, UV y el workspace | [Primeros pasos y desarrollo](../../docs/development.md) |
| Comprender fuentes, archivos y secretos | [Configuración transversal](../../docs/configuration.md) |
| Construir e inspeccionar el wheel | [Empaquetado](../../docs/packaging.md) |
| Actualizar versión y consumidores | [Versionamiento](../../docs/versioning.md) |

## Construcción y contenido del wheel

El resultado sigue el patrón:

```text
backend/dist/atlanticus_configuration-<version>-py3-none-any.whl
```

El wheel debe incluir `atlanticus/configuration/*.py` y `py.typed`. No debe contener `tests/`,
`commented/`, caches ni metadata de desarrollo.

El `pyproject.toml` actual no declara `readme`. Esto evita incorporar como descripción del wheel un
documento que depende de navegación e imágenes externas al directorio del package. La política de
metadata documental se resolverá transversalmente antes de publicar wheels definitivos.

## Actualización de versión

Antes de publicar una revisión deben evaluarse como contrato público:

- ambientes y variable reservada;
- precedencia local y fuentes desplegadas;
- schema de `secrets.json`;
- semántica de defaults, opcionales y sensibles;
- conversiones de `ResolvedConfiguration`;
- protocolo `SecretResolver`;
- jerarquía y sanitización de errores;
- exports públicos y `__version__`.

Una actualización debe mantener alineados:

1. `[project].version` en `pyproject.toml`;
2. `__version__` en source y mirror;
3. pins exactos y fuentes de los nueve consumidores;
4. `backend/uv.lock` y locks derivados;
5. pruebas y README;
6. wheels y artifacts regenerados.

Ruff, formato y pruebas se ejecutan antes y después del cambio conforme a
[Versionamiento](../../docs/versioning.md).

## Elementos no verificados o pendientes

- No se ejecutó el gate en esta revisión documental porque el entorno disponible no posee Python
  `3.14.2` y no se permite descargarlo automáticamente.
- Un manifiesto puede contener variables que no están declaradas en `specs`; actualmente se ignoran
  sin advertencia. Esto permite manifiestos compartidos, pero también puede ocultar nombres
  obsoletos o errores tipográficos.
- `json.loads()` no detecta nombres de campo repetidos dentro del mismo objeto JSON; conserva el
  último valor antes de que el schema valide la entrada.
- El módulo no valida la sintaxis Azure de `secret_name`; esa comprobación ocurre en el resolver
  concreto.
- No existe validación semántica transversal para URLs, paths, rangos o duraciones; cada consumidor
  debe aplicarla después del bootstrap.
- No se verificó resolución contra un Key Vault real como parte de esta revisión documental.
- El contrato backend `ENVIRONMENT` no se asume equivalente a la configuración de la capa web.

## Control documental

La versión `1.0.1` corresponde únicamente a este README. La versión técnica del wheel continúa
siendo la declarada por su `pyproject.toml`.

El documento se encuentra **Validado**. Su validación no publica una nueva versión de
`atlanticus-configuration` ni elimina los límites técnicos registrados.

---

[Volver a Backend](../README.md) · [Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)
