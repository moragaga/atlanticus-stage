<p align="right">
  <img src="assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Primeros pasos y desarrollo

[Volver al índice de documentación](README.md) · [Volver al README principal](../README.md)

Esta guía explica cómo preparar un entorno de desarrollo para Atlanticus, sincronizar un proyecto,
trabajar con su `.venv`, comprender sus `pyproject.toml` y crear módulos o aplicaciones backend
coherentes con la estructura existente.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.1.1` |
| Estado | Validado |
| Python requerido | `3.14.2` |
| Gestor de proyectos | UV |
| Audiencia | Desarrollo y mantenimiento técnico |

## Alcance

Esta guía es la fuente de verdad para:

- instalar UV y la versión de Python requerida;
- identificar el proyecto o workspace correcto;
- crear y sincronizar `.venv`;
- ejecutar herramientas dentro del entorno;
- entender la estructura mínima de una librería o aplicación backend;
- declarar nombres, imports y entrypoints;
- aplicar Ruff y ejecutar pruebas durante el desarrollo;
- mantener el espejo pedagógico comentado.

No explica configuración operacional, variables de entorno, ejecución funcional de procesos,
Docker, construcción de wheels, artifacts, versionamiento ni deployment. Esos procedimientos
pertenecen a sus respectivas guías transversales.

## Fundamentos verificados

El snapshot actual establece los siguientes contratos:

| Contrato | Decisión vigente |
|---|---|
| Python | Todos los proyectos declaran `requires-python = "==3.14.2"`. |
| Instalación de dependencias | UV; `pip` no forma parte del flujo documentado. |
| Descarga durante gates | Los validadores usan `--no-python-downloads`; Python debe existir previamente. |
| Locks | Cada workspace o proyecto autónomo mantiene su propio `uv.lock`. |
| Entornos | Cada workspace o proyecto autónomo crea su propia `.venv`. |
| Paquetes | Las librerías y aplicaciones construibles utilizan layout `src/`. |
| Build backend | Los paquetes actuales usan `setuptools.build_meta`. |
| Calidad | Ruff y Pytest están fijados como dependencias de desarrollo. |
| Distribución | Los paquetes son privados y usan `Private :: Do Not Upload`. |

La raíz de Atlanticus no contiene un `pyproject.toml` global. Por lo tanto, no existe una única
sincronización ni una `.venv` para todo el repositorio.

## 1. Instalar UV

UV se instala como herramienta independiente de Python. Atlanticus no utiliza `pip` para instalar
ni administrar sus proyectos.

### macOS y Linux

El instalador oficial actual de Astral es:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Windows PowerShell

El instalador oficial actual es:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

En ambientes corporativos no debe modificarse una política de ejecución ni descargarse un script
remoto sin la autorización correspondiente. Si existe un canal de instalación administrado por la
organización, ese mecanismo tiene prioridad.

Verificar la instalación:

```bash
uv --version
```

El repositorio actual no fija una versión mínima o exacta de UV. Por esa razón esta guía no inventa
un número. El instalador sin versión obtiene la versión vigente; para ambientes reproducibles debe
utilizarse una versión aprobada por la organización y registrarse posteriormente como política del
proyecto.

Referencias oficiales:

- [Instalación de UV](https://docs.astral.sh/uv/getting-started/installation/)
- [Proyectos administrados con UV](https://docs.astral.sh/uv/guides/projects/)

## 2. Instalar Python 3.14.2

Atlanticus requiere exactamente CPython `3.14.2`. Tener otra versión `3.14.x` no satisface el
contrato.

UV puede instalar la versión requerida:

```bash
uv python install 3.14.2
```

Localizar el intérprete instalado:

```bash
uv python find 3.14.2
```

Verificar la versión efectiva:

```bash
uv run --python 3.14.2 --no-project python --version
```

Los gates de Atlanticus agregan `--no-python-downloads`. Esa opción impide que una validación
descargue un intérprete inesperadamente y obliga a preparar Python antes de ejecutar pruebas o
builds.

Referencia oficial: [instalar y administrar Python con UV](https://docs.astral.sh/uv/guides/install-python/).

## 3. Elegir el contexto de trabajo

Antes de sincronizar se debe identificar quién es propietario del `pyproject.toml` y del
`uv.lock`.

| Contexto | Ejemplo | Entorno resultante |
|---|---|---|
| Workspace | `backend/`, `connectivity/`, `integrations/` | Una `.venv` compartida por sus miembros |
| Librería autónoma | `scopes/data-producers/core/` | Una `.venv` dentro del módulo |
| Capacidad ADA autónoma | `scopes/ada/kpis/core/` | Una `.venv` dentro del módulo |
| Aplicación backend | `scopes/ada/processes/kpis/` | Una `.venv` dentro del proceso |

La sincronización se ejecuta desde el directorio que contiene ambos archivos. No debe ejecutarse
desde la raíz suponiendo que UV descubrirá todos los proyectos.

## 4. Sincronizar un workspace

Ejemplo para preparar el backend completo:

```bash
cd backend
uv sync --all-packages --group dev --frozen
```

El mismo patrón se aplica desde `connectivity/` o `integrations/` cuando se necesita trabajar con
todos sus miembros.

| Opción | Efecto |
|---|---|
| `--all-packages` | Instala todos los miembros del workspace. |
| `--group dev` | Incluye Ruff, Pytest y otras herramientas declaradas para desarrollo. |
| `--frozen` | Usa el lock existente y falla si no coincide con `pyproject.toml`. |

`.python-version` y `requires-python` permiten que UV seleccione Python `3.14.2` sin repetirlo en el
comando. `uv sync --frozen` valida la coherencia del lock y no debe modificarlo.

## 5. Sincronizar un proyecto autónomo

Ejemplo con el proceso KPI:

```bash
cd scopes/ada/processes/kpis
uv sync --group dev --frozen
```

No se usa `--all-packages` porque este directorio representa un único proyecto. Sus dependencias
internas se resuelven mediante `[tool.uv.sources]` y rutas locales declaradas explícitamente.

## 6. Comprender `.venv`

`uv sync` crea `.venv` automáticamente si todavía no existe. No es necesario ejecutar `python -m
venv`, instalar dependencias manualmente ni crear un entorno desde la raíz.

La activación es opcional. El flujo recomendado utiliza `uv run`, porque selecciona el entorno del
proyecto actual sin depender del estado previo de la terminal.

Ejecutar Python dentro del entorno sincronizado:

```bash
uv run --frozen python --version
```

`--frozen` permite que UV prepare el entorno cuando sea necesario, pero impide que actualice el lock
como efecto secundario.

Para configurar un IDE, el intérprete se encuentra normalmente en:

| Plataforma | Intérprete |
|---|---|
| macOS y Linux | `.venv/bin/python` |
| Windows | `.venv\Scripts\python.exe` |

No se versiona `.venv`. Si el entorno deja de ser confiable, debe regenerarse desde `pyproject.toml`
y `uv.lock`.

### Comando cotidiano y gate oficial

Los comandos de una persona desarrolladora deben ser breves. Los validadores agregan restricciones
adicionales para demostrar que el mismo resultado puede reproducirse sin decisiones implícitas.

| Opción estricta | Por qué aparece en los gates |
|---|---|
| `--python 3.14.2` | Fuerza el intérprete exacto sin depender del descubrimiento de UV. |
| `--no-python-downloads` | Impide que una validación descargue Python silenciosamente. |
| `--no-sync` | Ejecuta sobre un entorno que el gate ya preparó explícitamente. |
| `--no-cache` | Obliga a reconstruir el contexto seleccionado desde cero. |
| `uv lock --check` | Comprueba el lock sin actualizarlo. |

Estas opciones no deben copiarse mecánicamente en cada comando cotidiano. La persona utiliza
`uv sync --frozen` y `uv run --frozen`; el validador oficial asume la responsabilidad del gate
estricto.

## 7. Estructura de los proyectos

### Librería construible

```text
example/
├── .python-version
├── pyproject.toml
├── uv.lock
├── README.md
├── src/
│   └── atlanticus/
│       └── example/
│           ├── __init__.py
│           └── py.typed
├── tests/
└── commented/
    └── atlanticus/
        └── example/
```

### Aplicación backend ejecutable

```text
example/
├── .python-version
├── pyproject.toml
├── uv.lock
├── README.md
├── src/
│   └── ada/
│       └── processes/
│           └── example/
│               ├── __init__.py
│               ├── __main__.py
│               ├── bootstrap.py
│               └── py.typed
├── tests/
├── commented/
└── scripts/
    ├── check.sh
    └── check.bat
```

No toda librería necesita `__main__.py`, `bootstrap.py`, un entrypoint ni scripts propios. Esos
elementos se incorporan solamente cuando el proyecto representa una aplicación ejecutable o posee
un ciclo de vida independiente.

## 8. Responsabilidad de `pyproject.toml`

| Sección | Responsabilidad |
|---|---|
| `[build-system]` | Define setuptools y el backend que construye el wheel. |
| `[project]` | Nombre de distribución, versión, descripción, Python y dependencias productivas. |
| `[dependency-groups]` | Herramientas exclusivas de desarrollo. |
| `[project.scripts]` | Comandos instalables y su callable de entrada. |
| `[tool.uv.sources]` | Resolución local de dependencias internas durante el desarrollo. |
| `[tool.uv.workspace]` | Miembros administrados conjuntamente por un workspace. |
| `[tool.setuptools.packages.find]` | Paquetes Python incluidos desde `src/`. |
| `[tool.setuptools.package-data]` | Archivos adicionales, como `py.typed`. |
| `[tool.pytest.ini_options]` | Descubrimiento y opciones de pruebas. |
| `[tool.ruff]` | Formato, lint, exclusiones y versión objetivo de Python. |
| `[tool.atlanticus.container]` | Contrato de ejecución utilizado al preparar contenedores. |

### Contrato base de una librería

El siguiente ejemplo representa la estructura esperada para un módulo nuevo. Las dependencias y el
namespace deben adaptarse al área propietaria.

```toml
[build-system]
requires = ["setuptools==83.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "atlanticus-example"
version = "1.0.0"
description = "Reusable example capability for Atlanticus."
readme = "README.md"
requires-python = "==3.14.2"
dependencies = []
classifiers = ["Private :: Do Not Upload"]

[dependency-groups]
dev = ["pytest==9.1.1", "ruff==0.15.22"]

[tool.setuptools.packages.find]
where = ["src"]
include = ["atlanticus.example*"]
namespaces = true

[tool.setuptools.package-data]
"atlanticus.example" = ["py.typed"]

[tool.pytest.ini_options]
addopts = "-q --import-mode=importlib"
testpaths = ["tests"]
```

Si el módulo pertenece a un workspace, las dependencias de desarrollo y la configuración de Ruff
pueden residir en el `pyproject.toml` del workspace para evitar duplicación. Un proyecto autónomo
debe declararlas localmente.

## 9. Crear el nombre de un módulo

Un módulo posee varias identidades relacionadas, pero no intercambiables:

| Identidad | Convención | Ejemplo |
|---|---|---|
| Ruta física | Directorios en `kebab-case` | `scopes/ada/processes/kpis-delivery/` |
| Nombre del proyecto | Distribución normalizada con guiones | `ada-kpis-delivery-process` |
| Nombre del wheel | Derivado por el build con guiones bajos | `ada_kpis_delivery_process-1.0.0-py3-none-any.whl` |
| Paquete importable | Namespace con puntos y `snake_case` | `ada.processes.kpis_delivery` |
| Comando ejecutable | Nombre corto en `kebab-case` | `ada-kpis-delivery` |
| Callable de entrada | Ruta importable y función | `ada.processes.kpis_delivery.bootstrap:main` |

El nombre se define en este orden:

1. Identificar si la capacidad es genérica de Atlanticus o específica de un scope.
2. Elegir el área propietaria y su ruta física.
3. Definir un `project.name` único dentro del repositorio.
4. Definir el namespace público que consumirá el código.
5. Agregar un comando solamente si el proyecto es ejecutable.
6. Documentar el mapeo completo en el README del módulo.
7. Validar que el import y el entrypoint funcionen después de sincronizar.

Una librería genérica utiliza normalmente el prefijo `atlanticus-`. Una capacidad específica de ADA
utiliza `ada-`. El sufijo `-process` distingue actualmente las distribuciones correspondientes a
procesos, mientras el comando omite ese sufijo.

Estas convenciones describen el repositorio vigente; no deben extenderse a una nueva familia de
proyectos sin revisar primero su frontera y propietario.

## 10. Declarar una aplicación y su bootstrap

Una aplicación backend declara el comando instalable en `[project.scripts]`:

```toml
[project.scripts]
ada-example = "ada.processes.example.bootstrap:main"
```

La clave `ada-example` es el comando que UV instala dentro de `.venv`. El valor indica el módulo
importable y el callable que debe invocarse.

El patrón actual separa dos responsabilidades:

- `run(...)` contiene la ejecución invocable y comprobable desde pruebas;
- `main()` adapta esa ejecución al entrypoint instalado.

`__main__.py` permite que la misma aplicación pueda exponerse como módulo Python y delega en el
mismo `main`, evitando dos composiciones diferentes.

El contrato de contenedor debe utilizar un comando ya declarado:

```toml
[tool.atlanticus.container]
command = "ada-example"
system-profile = "base"
```

El bundler comprueba que `command` exista en `[project.scripts]`. Los perfiles admitidos por el
snapshot actual son:

| Perfil | Uso |
|---|---|
| `base` | Procesos que no requieren dependencias de sistema adicionales. |
| `sqlserver` | Procesos cuyo contenedor necesita las dependencias del perfil SQL Server. |

No debe elegirse `sqlserver` por el nombre del módulo ni agregarse un perfil nuevo sin modificar y
validar primero el contrato de deployment que lo consume.

La capa web unificada no está presente en el snapshot backend utilizado para esta guía. Su contrato
de bootstrap se documentará cuando su source y sus artifacts estén integrados y verificados; no se
asume que deba copiar exactamente el modelo de un proceso.

## 11. Dependencias internas y locks

Las dependencias productivas se declaran en `[project].dependencies`. Las herramientas de
desarrollo se declaran en `[dependency-groups].dev`.

Durante el desarrollo, una dependencia interna puede resolverse por workspace:

```toml
[tool.uv.sources]
atlanticus-kernel = { workspace = true }
```

O mediante una ruta explícita:

```toml
[tool.uv.sources]
atlanticus-job-runtime = { path = "../../../../backend/runtime", editable = true }
```

La misma dependencia debe existir en `[project].dependencies`; `[tool.uv.sources]` solo define de
dónde se obtiene en el entorno de desarrollo.

Reglas obligatorias:

- no editar `uv.lock` manualmente;
- no agregar dependencias con `pip`;
- no usar una ruta local como contrato transportable del artifact;
- no omitir la versión de una dependencia productiva interna;
- no ejecutar una actualización general para resolver un cambio aislado;
- revisar el impacto sobre consumidores antes de cambiar una versión compartida.

La actualización efectiva de dependencias y versiones se explicará en `docs/versioning.md`.

## 12. Ruff y pruebas durante el desarrollo

Ruff define el formato y análisis estático común. En el snapshot actual se utilizan, entre otras,
las reglas `E`, `W`, `F`, `I`, `B` y, en varios módulos autónomos, `SIM`.

Después de sincronizar el workspace backend, un ciclo directo sobre `kernel` puede ejecutarse así:

```bash
uv run --frozen ruff check --fix kernel
uv run --frozen ruff format kernel
uv run --frozen ruff check kernel
uv run --frozen ruff format --check kernel
uv run --frozen pytest kernel/tests
```

Las dos primeras operaciones pueden modificar archivos. Las dos siguientes verifican que el estado
final ya esté limpio. Pytest comprueba comportamiento; Ruff no reemplaza las pruebas.

Cuando existe un validador oficial, ese es el gate recomendado porque también comprueba lock,
sincronización, imports y build. Desde `backend/`:

```bash
./scripts/validation/check.sh kernel --clean
```

El validador no es pasivo: elimina salidas generadas del contexto seleccionado y aplica fixes y
formato antes de ejecutar las comprobaciones finales. Los equivalentes BAT se documentarán en una
revisión posterior sin suponer que todos los scripts Bash poseen una traducción vigente.

Las pruebas de integración que necesitan Docker o infraestructura externa no forman parte de este
primer ciclo. Deben ejecutarse mediante el gate específico y registrarse como verificadas solamente
cuando los servicios requeridos estén disponibles.

## 13. Espejo pedagógico comentado

El código productivo permanece limpio y sin comentarios pedagógicos. Cuando un módulo mantiene
`commented/`, ese directorio conserva un espejo equivalente con explicaciones en español.

El espejo debe:

- mantener la misma estructura funcional que `src/`;
- conservar imports, firmas, decisiones y comportamiento;
- actualizarse en el mismo cambio que producción;
- compilar correctamente;
- respetar las pruebas de paridad que posea el módulo;
- quedar fuera de los wheels y artifacts.

Excluir `commented/` del recorrido normal de Ruff no autoriza a abandonarlo. Los validadores pueden
procesar sus archivos de forma explícita para mantener formato y sintaxis sin incorporarlos a la
distribución.

## 14. Flujo para incorporar un módulo nuevo

1. Confirmar que existe una responsabilidad independiente o reutilización real.
2. Identificar el área propietaria y sus dependencias permitidas.
3. Definir ruta, distribución, import y, si corresponde, entrypoint.
4. Crear el layout `src/`, pruebas y espejo comentado.
5. Declarar `pyproject.toml` y `.python-version` con Python `3.14.2`.
6. Incorporarlo al workspace propietario o mantenerlo como proyecto autónomo.
7. Generar su `uv.lock` sin actualizar dependencias ajenas.
8. Sincronizar una `.venv` desde el contexto correcto.
9. Implementar primero sus contratos y luego sus consumidores.
10. Ejecutar Ruff, formato, pruebas e import smoke.
11. Documentar sus capacidades, límites, configuración y nombres.
12. Construir y validar su wheel mediante la guía de empaquetado.

No debe crearse una abstracción, clase, workspace o aplicación nueva únicamente para replicar una
estructura existente. La forma del proyecto depende de su responsabilidad y ciclo de vida.

## 15. Problemas frecuentes

| Síntoma | Causa probable | Acción |
|---|---|---|
| UV no encuentra Python | `3.14.2` no está instalado o visible. | Instalarlo y comprobarlo con `uv python find 3.14.2`. |
| `--frozen` falla | `pyproject.toml` y `uv.lock` no coinciden. | No borrar el error; determinar si faltó actualizar el lock de forma deliberada. |
| Se crea `.venv` en la carpeta equivocada | La sincronización se ejecutó desde un contexto sin el proyecto esperado. | Eliminar el entorno generado mediante el procedimiento de limpieza y sincronizar desde el propietario. |
| Un import interno no se resuelve | Falta la dependencia o su entrada en `[tool.uv.sources]`. | Revisar ambos contratos; no modificar `PYTHONPATH` como solución permanente. |
| El comando no aparece | Falta `[project.scripts]` o no se sincronizó nuevamente. | Corregir el entrypoint y resincronizar el proyecto. |
| El bundler rechaza el proceso | El comando del contenedor no coincide con `[project.scripts]` o el perfil es inválido. | Corregir el contrato declarado. |
| Ruff cambia archivos durante el gate | El validador aplica fixes y formato antes de comprobar. | Revisar los cambios y repetir el gate hasta obtener un estado estable. |

## Checklist de preparación

- [ ] UV está instalado y disponible en `PATH`.
- [ ] Python `3.14.2` está instalado y UV puede localizarlo.
- [ ] La terminal está ubicada en el workspace o proyecto correcto.
- [ ] `pyproject.toml` y `uv.lock` existen y son coherentes.
- [ ] `uv sync --frozen` finaliza correctamente.
- [ ] La `.venv` pertenece al contexto esperado.
- [ ] El IDE utiliza el intérprete de esa `.venv`.
- [ ] Ruff y formato quedan limpios después de aplicar fixes.
- [ ] Las pruebas del módulo quedan verdes.
- [ ] El import público y el entrypoint, si existe, son resolubles.
- [ ] El espejo comentado está actualizado.

## Elementos no verificados

- El snapshot no fija una versión exacta de UV.
- La guía todavía prioriza los flujos Bash de validación.
- Los procedimientos BAT se incorporarán después de contrastar cada equivalente.
- El bootstrap de la capa web se documentará cuando su estructura unificada esté disponible en el
  repositorio revisado.
- La instalación corporativa puede imponer restricciones adicionales sobre descargas, PATH,
  proxies o políticas de PowerShell.

---

[Volver al índice de documentación](README.md) · [Volver al README principal](../README.md)
