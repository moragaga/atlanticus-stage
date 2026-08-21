<p align="right">
  <img src="../../docs/assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Atlanticus JSON

[Volver a Backend](../README.md) · [Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)

`atlanticus-json` define primitivas estrictas y determinísticas para documentos JSON, junto con un
store local que realiza reemplazos atómicos. Su objetivo es que los consumidores no interpreten
silenciosamente documentos ambiguos, corruptos o parcialmente escritos.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.0.0` |
| Estado | En revisión |
| Ruta física | `backend/json/` |
| Distribución | `atlanticus-json` |
| Import público | `atlanticus.json` |
| Versión técnica actual | `0.1.0` |
| Python requerido | `3.14.2` |
| Dependencias productivas | Ninguna fuera de la biblioteca estándar |
| Tipo | Wheel tipado y reutilizable |

La versión técnica se obtiene de `[project].version` en `backend/json/pyproject.toml`. No debe
actualizarse modificando únicamente este README.

## Propósito

El módulo proporciona dos capacidades relacionadas:

1. validar, normalizar, codificar y decodificar documentos JSON bajo un contrato estricto;
2. leer y reemplazar esos documentos en el sistema de archivos sin exponer escrituras parciales a
   lectores concurrentes del mismo filesystem.

Es una librería, no una aplicación. No declara entrypoints ni procesos ejecutables.

## Límites

Atlanticus JSON deliberadamente:

- no define schemas funcionales de KPI, configuración, state o cualquier dominio;
- no convierte dataclasses, fechas, `Decimal`, `UUID`, `Path` ni enumeraciones;
- no sanitiza ni redacta secretos;
- no cifra ni firma documentos;
- no calcula rutas de aplicación;
- no impone una raíz permitida para los archivos;
- no mantiene historial ni revisiones;
- no implementa almacenamiento cloud;
- no aplica límites de tamaño total del documento;
- no coordina exclusión entre procesos o equipos diferentes.

`DataSanitizer` de Kernel y Atlanticus JSON resuelven problemas distintos. El primero prepara
diagnósticos acotados; JSON rechaza valores que no pertenecen explícitamente a su contrato y no debe
usarse como mecanismo de seguridad.

## API pública

Solo los símbolos exportados por `atlanticus.json.__init__` forman parte del contrato público.

| Símbolo | Tipo | Responsabilidad |
|---|---|---|
| `JsonScalar` | Alias de tipo | `None`, booleano, entero, float finito o texto. |
| `JsonValue` | Alias recursivo | Escalar, lista de valores o objeto con claves de texto. |
| `JsonDocument` | Alias de tipo | Objeto JSON raíz representado como `dict`. |
| `normalize_json_document()` | Función | Valida y copia un mapping bajo el contrato JSON. |
| `encode_json_document()` | Función | Produce bytes UTF-8 determinísticos. |
| `decode_json_document()` | Función | Decodifica bytes estrictos a un documento. |
| `JsonDocumentStore` | Clase | Existencia, lectura, reemplazo y escritura única local. |
| `JsonWriteOnceStatus` | `StrEnum` | Resultado `created` o `unchanged`. |
| `JsonError` | Excepción base | Raíz de errores propios del módulo. |
| `JsonValidationError` | Excepción | Valor Python no representable por el contrato. |
| `JsonReadError` | Excepción | Fallo de inspección o lectura física. |
| `JsonWriteError` | Excepción | Fallo durante una escritura física. |
| `JsonCorruptionError` | Excepción | Bytes existentes que no forman un documento válido. |
| `JsonConflictError` | Excepción | `write_once` encontró contenido diferente. |
| `__version__` | Texto | Versión expuesta en runtime. |

## 1. Contrato del documento

La raíz debe ser siempre un objeto JSON. No se admiten escalares ni arrays como documento raíz:

```python
from atlanticus.json import normalize_json_document

document = normalize_json_document(
    {
        'application': 'ada',
        'revision': 42,
        'enabled': True,
        'items': ['kpi-a', 'kpi-b'],
    }
)
```

### Valores admitidos

| Valor Python | Resultado JSON |
|---|---|
| `None` | `null` |
| `bool` | `true` o `false` |
| `int` | Número entero |
| `float` finito | Número |
| `str` UTF-8 válido | Texto |
| `Mapping` con claves `str` | Objeto |
| `Sequence`, excepto texto y bytes | Array normalizado a `list` |

Tuplas y otras secuencias compatibles se copian como listas. Sets, bytes, objetos arbitrarios y
tipos de dominio se rechazan; el consumidor debe transformarlos conscientemente antes de llamar al
módulo.

### Validaciones estrictas

Se rechazan:

- claves que no sean texto;
- strings o claves que no puedan codificarse como UTF-8;
- `NaN`, `Infinity` y `-Infinity`;
- referencias cíclicas;
- valores no soportados;
- estructuras que superen la profundidad máxima de 64 niveles.

La normalización crea nuevos diccionarios y listas. Modificar después la estructura recibida no
cambia el documento ya normalizado.

El límite de profundidad evita recursión sin control, pero no limita cantidad de claves, elementos,
longitud de textos ni bytes finales. El consumidor sigue siendo responsable de controlar el tamaño
de sus contratos.

## 2. Codificación determinística

```python
from atlanticus.json import encode_json_document

content = encode_json_document({'z': 2, 'a': 'área'})
```

La codificación:

- ordena las claves alfabéticamente;
- utiliza separadores compactos;
- conserva caracteres Unicode en UTF-8;
- prohíbe constantes numéricas no finitas;
- retorna `bytes`;
- no agrega salto de línea final.

Dos documentos equivalentes con distinto orden de claves producen los mismos bytes. Esta
determinación es útil para firmas, comparaciones y persistencia reproducible, pero el módulo no
calcula hashes ni firmas por sí mismo.

El módulo acepta enteros Python sin imponer el rango interoperable de 53 bits utilizado por algunos
consumidores JavaScript. Si el documento cruza esa frontera, el schema propietario debe restringir
el rango o serializar el identificador como texto.

## 3. Decodificación estricta

```python
from atlanticus.json import decode_json_document

document = decode_json_document(b'{"revision":42}')
```

`decode_json_document()` acepta exclusivamente `bytes`. Entregar otro tipo produce `TypeError`.

Se rechazan como corrupción:

- bytes que no sean UTF-8;
- sintaxis JSON inválida;
- claves duplicadas dentro del mismo objeto;
- constantes `NaN` o `Infinity`;
- un array o escalar como raíz;
- valores que fallen la normalización posterior.

Las claves duplicadas no utilizan la política habitual de “gana la última”. El documento completo
se considera ambiguo y produce `JsonCorruptionError`.

## 4. Store local

```python
from pathlib import Path

from atlanticus.json import JsonDocumentStore

store = JsonDocumentStore()
path = Path('/app/volume/ada/evaluations/latest.json')
```

Todas las operaciones exigen una ruta absoluta. El módulo no resuelve variables, no anexa
`APPLICATION` y no restringe la ruta a un volumen concreto.

Una ruta absoluta puede incluir symlinks o segmentos definidos por el consumidor. El store no actúa
como sandbox: la capa propietaria debe construir rutas confiables y evitar entradas libres de un
usuario.

### Existencia

```python
exists = store.exists(path)
```

Retorna el resultado del filesystem. Un fallo de inspección se transforma en `JsonReadError`.

### Lectura

```python
document = store.read(path)
```

- retorna `None` únicamente cuando el archivo no existe;
- retorna un `JsonDocument` cuando los bytes son válidos;
- propaga `JsonCorruptionError` si el archivo existe, pero es inválido;
- transforma otros fallos físicos en `JsonReadError`.

Un documento corrupto nunca se interpreta como ausencia. El consumidor debe detenerse o aplicar su
política explícita de recuperación.

### Reemplazo

```python
store.replace(path, {'revision': 43, 'status': 'ready'})
```

El reemplazo:

1. valida y codifica el documento antes de adquirir el lock de escritura;
2. crea los directorios padres cuando faltan;
3. escribe un temporal oculto y exclusivo en el mismo directorio;
4. fuerza sus bytes mediante `fsync`;
5. confirma con `os.replace`;
6. fuerza el directorio y limpia el temporal restante.

El archivo se escribe con modo solicitado `0640`, sujeto al `umask` y comportamiento del sistema
operativo. El store no ejecuta `chmod` ni cambia propietario o ACL.

`os.replace` evita que lectores del mismo filesystem observen contenido parcial: ven la versión
anterior o la nueva. Esta atomicidad no significa transacción distribuida, validación funcional ni
replicación.

### Escritura única

```python
from atlanticus.json import JsonWriteOnceStatus

status = store.write_once(path, {'evaluation_id': 'kpi-a@2026-08-21T10:00:00Z'})

if status is JsonWriteOnceStatus.CREATED:
    pass
```

La semántica actual es:

| Estado o error | Condición |
|---|---|
| `CREATED` | La ruta no existía y se confirmó el documento. |
| `UNCHANGED` | El documento existente compara igual al normalizado entrante. |
| `JsonConflictError` | La ruta existe con contenido que compara diferente. |
| `JsonCorruptionError` | La ruta existe, pero sus bytes no son un documento válido. |

El orden de las claves no produce conflicto. `write_once` protege contra mutaciones accidentales
dentro de una misma instancia del store, pero sus garantías tienen límites importantes.

## Garantías y límites de concurrencia

`JsonDocumentStore` utiliza un `threading.RLock` interno.

| Escenario | Garantía actual |
|---|---|
| Varios threads que comparten la misma instancia | Las escrituras se serializan. |
| Varias instancias dentro del mismo proceso | No comparten lock. |
| Varios procesos o contenedores | No existe file lock ni operación create-if-absent atómica. |
| Lectores concurrentes durante `replace` | Observan un documento completo anterior o nuevo. |

Por lo tanto, `write_once` no garantiza exclusión interproceso. Dos escritores independientes
pueden leer ausencia y competir por el reemplazo. Una lease del Job Runtime puede reducir esa
posibilidad en una aplicación concreta, pero la garantía no pertenece a Atlanticus JSON.

También se compara contenido mediante igualdad de Python. Valores numéricamente iguales pueden
comparar como equivalentes aunque su tipo sea diferente; el caso más delicado es `True == 1`. Esta
semántica debe corregirse o cubrirse con un schema de dominio antes de usar `write_once` como
garantía general de inmutabilidad tipada.

## Durabilidad y resultado incierto

El archivo temporal se fuerza antes del reemplazo y el directorio se fuerza después. Sin embargo,
si `os.replace()` termina y luego falla el `fsync` del directorio, el método produce
`JsonWriteError`, aunque el documento nuevo podría estar visible en la ruta final.

Ante ese error, el consumidor no debe asumir automáticamente que conserva el contenido anterior.
Debe leer nuevamente la ruta y reconciliar el resultado conforme a su contrato. Una futura mejora
podría distinguir un fallo previo al commit de un resultado de durabilidad incierta.

## Jerarquía de errores

| Error | También hereda de | Uso |
|---|---|---|
| `JsonError` | `Exception` | Captura general del módulo. |
| `JsonValidationError` | `ValueError` | Argumento o documento Python inválido. |
| `JsonReadError` | `OSError` | Inspección o lectura física fallida. |
| `JsonWriteError` | `OSError` | Escritura o confirmación fallida. |
| `JsonConflictError` | `JsonWriteError` | Inmutabilidad lógica vulnerada. |
| `JsonCorruptionError` | `JsonError` | Contenido existente inválido o ambiguo. |

Los mensajes técnicos permanecen en inglés. El consumidor puede capturar una categoría concreta
sin perder compatibilidad con `ValueError` u `OSError` cuando corresponda.

## Dependencias y consumidores

El wheel no depende de otros módulos de Atlanticus. Los consumidores declarados actualmente son:

- `ada-kpis-persistence`;
- el proceso `kpis`;
- el proceso `kpis-delivery`.

`kpis-historian` también importa `JsonDocumentStore` directamente, pero su
`[project].dependencies` no declara `atlanticus-json`; hoy lo recibe transitivamente mediante
`ada-kpis-persistence` y mantiene una fuente UV local. Esto es deuda técnica del consumidor: una
dependencia importada directamente debe declararse directamente.

Los usos actuales se concentran en evaluaciones KPI y su composición. JSON no conoce esos modelos;
solo recibe mappings construidos por la capability propietaria.

## Source y mirror pedagógico

```text
backend/json/
├── pyproject.toml
├── src/atlanticus/json/
│   ├── __init__.py
│   ├── errors.py
│   ├── serialization.py
│   ├── store.py
│   └── py.typed
├── commented/atlanticus/json/
└── tests/
```

El código productivo vive en `src/`. `commented/` conserva el espejo pedagógico en español y queda
fuera del wheel. `test_json_commented_mirror.py` compara archivos y tokens de Python para impedir
divergencias funcionales.

## Pruebas y validación

El snapshot contiene 16 funciones de prueba que Pytest expande a 22 casos por parametrización:

| Archivo | Contrato validado |
|---|---|
| `test_serialization.py` | Determinismo, copia, tipos inválidos, ciclos y corrupción. |
| `test_store.py` | Lectura, reemplazo, write-once, fallos y lectores concurrentes. |
| `test_json_commented_mirror.py` | Archivos y equivalencia source/mirror. |

Las pruebas actuales cubren threads que comparten una instancia, pero no varios procesos,
filesystem de red, resultado incierto después de `os.replace` ni la equivalencia `True == 1`.

El gate oficial es `backend/scripts/validation/check.sh json --clean`. Aplica Ruff y formato antes
de comprobarlos, ejecuta pruebas, valida `import atlanticus.json`, construye un wheel y verifica su
presencia en `backend/dist/`.

| Necesidad | Documento propietario |
|---|---|
| Preparar el workspace y ejecutar pruebas | [Primeros pasos y desarrollo](../../docs/development.md) |
| Construir e inspeccionar el wheel | [Empaquetado](../../docs/packaging.md) |
| Actualizar versión y consumidores | [Versionamiento](../../docs/versioning.md) |

## Construcción y contenido del wheel

El resultado sigue el patrón:

```text
backend/dist/atlanticus_json-<version>-py3-none-any.whl
```

El wheel debe incluir `atlanticus/json/*.py` y `py.typed`. No debe contener `tests/`, `commented/`,
caches ni metadata de desarrollo.

El `pyproject.toml` actual todavía no declara `readme = "README.md"`; por eso crear este documento
no lo incorpora automáticamente a la metadata del wheel. Agregar esa referencia sería un cambio de
empaquetado separado que debe validarse técnicamente.

## Actualización de versión

Antes de publicar una revisión deben evaluarse como contrato público:

- tipos JSON admitidos y forma obligatoria del documento raíz;
- codificación determinística y reglas de UTF-8;
- detección de claves duplicadas, constantes inválidas y ciclos;
- profundidad máxima;
- rutas absolutas y semántica de lectura;
- atomicidad, durabilidad y permisos de reemplazo;
- estados y conflictos de `write_once`;
- jerarquía y mensajes de errores;
- exports públicos y `__version__`.

Una actualización debe mantener alineados:

1. `[project].version` en `pyproject.toml`;
2. `__version__` en source y mirror;
3. pins exactos y fuentes de consumidores;
4. `backend/uv.lock`;
5. pruebas y README;
6. wheels y artifacts derivados.

Ruff, formato y pruebas se ejecutan antes y después del cambio conforme a
[Versionamiento](../../docs/versioning.md).

## Elementos no verificados o pendientes

- No se ejecutó el gate porque el entorno documental no dispone de Python `3.14.2` y no se permite
  descargarlo automáticamente.
- `write_once` no posee exclusión interproceso ni prueba de competencia entre instancias.
- La igualdad actual puede confundir booleanos y enteros equivalentes en Python.
- Un fallo de `fsync` posterior a `os.replace` puede producir un resultado de commit incierto.
- No existe límite total de bytes para un documento.
- No se verificó durabilidad en Windows, filesystem de red ni volúmenes cloud montados.
- `kpis-historian` debe declarar su dependencia directa.
- Incorporar el README a la metadata del wheel requiere modificar y validar `pyproject.toml`.

## Control documental

La versión `1.0.0` corresponde únicamente a este README. La versión técnica del wheel continúa
siendo la declarada por su `pyproject.toml`.

El documento permanece **En revisión**. Su aprobación no corrige las limitaciones técnicas
registradas ni publica una nueva versión de Atlanticus JSON.

---

[Volver a Backend](../README.md) · [Volver al índice de documentación](../../docs/README.md) ·
[Volver al README principal](../../README.md)
