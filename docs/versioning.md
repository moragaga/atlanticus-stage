<p align="right">
  <img src="assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Versionamiento de Atlanticus

[Volver al índice de documentación](README.md) · [Volver al README principal](../README.md)

Esta guía define cómo decidir, propagar y validar las versiones de librerías y aplicaciones
backend de Atlanticus. Su objetivo es que un número de versión identifique un contrato técnico
reproducible y no sea únicamente una etiqueta modificada antes de generar un wheel o artifact.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.0.1` |
| Estado | Validado |
| Política propuesta | Semantic Versioning `MAJOR.MINOR.PATCH` |
| Fuente primaria | `[project].version` del `pyproject.toml` propietario |
| Audiencia | Desarrollo, mantenimiento técnico y arquitectura |

## Alcance

Esta guía es la fuente de verdad para:

- diferenciar versiones documentales, versiones de proyectos y versiones externas;
- decidir si un cambio requiere `PATCH`, `MINOR` o `MAJOR`;
- manejar proyectos que todavía utilizan versiones `0.x`;
- localizar y actualizar pins internos exactos;
- regenerar únicamente los locks afectados;
- mantener alineados `pyproject.toml`, `__version__`, mirrors y pruebas;
- validar una nueva versión mediante Ruff, formato, pruebas y empaquetado;
- impedir que dos entregas diferentes compartan la misma identidad de versión.

No establece un registry, una convención de tags Git, un changelog global, ramas de release ni un
pipeline de publicación. Esos mecanismos no están definidos en el snapshot actual y deben
aprobarse antes de documentarlos como contrato oficial.

## Fundamentos verificados

El repositorio actual establece estos hechos:

| Contrato | Estado actual |
|---|---|
| Unidad de versión | Cada librería o aplicación construible declara su propia versión. |
| Versión global | Atlanticus no posee una versión única para todo el monorepo. |
| Fuente de build | El wheel y el artifact obtienen la versión desde `[project].version`. |
| Exposición runtime | La mayoría de los packages también declara `__version__` manualmente. |
| Mirrors y pruebas | Varias versiones se repiten en `commented/` y pruebas del API público. |
| Dependencias internas | Se utilizan pins exactos `package==version`. |
| Coherencia actual | Los pins internos revisados coinciden con las versiones de sus proyectos. |
| Locks | Cada workspace comparte un lock; cada proyecto autónomo mantiene el suyo. |
| Automatización | No existe un comando oficial para incrementar y propagar versiones. |
| Publicación | Los packages están marcados `Private :: Do Not Upload`. |

La repetición manual permite un API `__version__` simple, pero también crea riesgo de divergencia.
Mientras no exista una herramienta de actualización validada, cada release debe comprobar todas
las representaciones de su versión.

## 1. Tres categorías de versión

### Versión del documento

Cada Markdown mantiene una versión documental independiente. Identifica la revisión funcional del
texto y no modifica ningún package.

| Cambio documental | Incremento recomendado |
|---|---|
| Ortografía, enlace o precisión menor | `PATCH` |
| Nueva sección o procedimiento compatible | `MINOR` |
| Cambio del alcance o contrato documental | `MAJOR` |

Validar un documento puede incrementar `PATCH` cuando solo cambia su estado y navegación. Un
cambio exclusivo de documentación no requiere modificar la versión de una librería o proceso.

### Versión de librería o aplicación

Pertenece al proyecto que contiene el `pyproject.toml` construible. Ejemplos de unidades
independientes son una librería de backend, un connector, una capacidad KPI y un proceso ADA.

La versión de un proceso identifica su composición ejecutable, aunque el artifact conserve el
source de la aplicación en lugar de construir un wheel para ella.

Los `pyproject.toml` agregadores de `backend/` y `connectivity/` declaran `package = false`. Sus
valores no sustituyen las versiones de los miembros ni representan una release global.

### Versión externa

Corresponde a Python, UV, Ruff, Pytest, Azure SDKs y demás dependencias de terceros. No debe
reescribirse para hacerla coincidir con Atlanticus.

Actualizar una dependencia externa es un cambio técnico separado: requiere evaluar compatibilidad,
actualizar el contrato propietario y regenerar su lock. No se aprovecha una release para subir
dependencias externas sin una necesidad explícita.

## 2. Contrato público versionado

Semantic Versioning solo tiene sentido cuando se identifica qué comportamiento se promete a los
consumidores. En Atlanticus el contrato público no se limita a funciones importables.

| Frontera | Ejemplos de contrato versionado |
|---|---|
| Python | Imports públicos, clases, funciones, firmas, tipos, excepciones y semántica. |
| Configuración | Variables, obligatoriedad, defaults, manifiestos y nombres reservados. |
| CLI | EntryPoint, argumentos, códigos de salida y significado de `--run-once`. |
| Datos | Schemas, nombres de campos, particiones, rutas y reglas de compatibilidad. |
| Persistencia | State, leases, manifests, revisiones y formatos serializados. |
| Observabilidad | Eventos, facts públicos y campos consumidos por soporte o monitoreo. |
| Empaquetado | Nombre de distribución, wheel, artifact y contrato de contenedor. |
| Operación | Comportamientos que otro proceso o plataforma necesita para integrarse. |

Una función privada puede cambiar sin afectar SemVer. Una variable requerida nueva, un campo
eliminado o una ruta de dataset diferente sí pueden romper consumidores aunque el código Python
público no haya cambiado.

Referencia normativa: [Semantic Versioning 2.0.0](https://semver.org/).

## 3. Decidir el incremento

### `PATCH`

Corrige un comportamiento incorrecto sin romper el contrato existente.

Ejemplos:

- corregir un cálculo que entregaba un resultado erróneo;
- evitar una excepción no intencional para una entrada ya válida;
- corregir una fuga de recursos sin cambiar el API;
- aplicar una corrección de seguridad compatible;
- actualizar un pin interno requerido sin alterar el contrato del consumidor.

Si un cambio interno no necesita entregarse, no es obligatorio crear una release solo por haber
refactorizado. Si se entrega un nuevo wheel o artifact con contenido diferente, debe utilizar una
versión nueva aunque el cambio no sea visible para el consumidor.

### `MINOR`

Agrega una capacidad compatible con consumidores existentes.

Ejemplos:

- nueva función o clase pública sin retirar las anteriores;
- parámetro opcional con un default compatible;
- nueva variable opcional con comportamiento seguro por defecto;
- nuevo evento o campo opcional que consumidores antiguos pueden ignorar;
- nueva modalidad operacional que no cambia la existente;
- marcar una capacidad como deprecada sin eliminarla.

### `MAJOR`

Modifica de forma incompatible un contrato público estable.

Ejemplos:

- eliminar o renombrar un import público;
- cambiar una firma o significado existente;
- agregar una variable obligatoria sin fallback compatible;
- cambiar entrypoint, argumentos o códigos de salida;
- modificar de forma incompatible un schema, una partición o una ruta persistida;
- eliminar soporte para una versión de Python;
- retirar una capacidad previamente deprecada;
- cambiar un formato de state o manifiesto sin migración compatible.

Ante duda entre `PATCH` y `MINOR`, se debe evaluar si el consumidor puede obtener un comportamiento
nuevo observable. Ante duda entre `MINOR` y `MAJOR`, se debe demostrar que un consumidor válido de
la versión anterior continúa funcionando sin cambios. Si no puede demostrarse, corresponde
`MAJOR`.

## 4. Versiones `0.x` y transición a `1.0.0`

El snapshot actual contiene packages en desarrollo inicial bajo `0.x`. En SemVer, `1.0.0` declara
el primer contrato público estable; no debe utilizarse como una normalización cosmética.

Mientras un package permanezca en `0.MINOR.PATCH`, Atlanticus aplicará esta interpretación:

| Cambio | Incremento durante `0.x` |
|---|---|
| Corrección compatible | `PATCH` |
| Capacidad compatible relevante | `MINOR` |
| Cambio incompatible | `MINOR` y documentación explícita de la ruptura |

Cuando el contrato de un package haya sido documentado, probado y aprobado como estable, puede
establecerse su `1.0.0`. No todos los packages deben alcanzar `1.0.0` simultáneamente.

La futura actualización de packages a `1.0.0` es una migración técnica: debe modificar código,
pins, locks, pruebas y artifacts. No se realiza alterando únicamente los números mostrados en la
documentación.

## 5. Fuentes que deben permanecer alineadas

`[project].version` es la fuente primaria para build y metadata:

```toml
[project]
name = "atlanticus-example"
version = "<next-version>"
```

Cuando el package expone versión en runtime, también existe:

```python
__version__ = '<next-version>'
```

La actualización debe revisar únicamente el proyecto propietario:

1. `pyproject.toml`;
2. `src/**/__init__.py`, si expone `__version__`;
3. `commented/**/__init__.py`, si existe el mirror;
4. pruebas que validan una versión literal;
5. README del módulo, cuando muestra la versión actual;
6. consumidores que fijan la distribución con `==`;
7. locks propietarios y consumidores;
8. artifacts derivados.

No todos los packages exponen `__version__`. No debe agregarse únicamente para uniformar el
repositorio durante una actualización documental.

Tampoco debe hacerse un reemplazo global de un número como `0.1.0`: varios proyectos y
dependencias externas pueden compartirlo sin pertenecer a la misma release.

## 6. Encontrar consumidores internos

Antes de cambiar una versión se debe buscar el nombre de distribución, no el namespace importable.

Ejemplo:

```bash
rg -n 'atlanticus-configuration==' -g 'pyproject.toml'
```

El resultado identifica consumidores directos. Después de actualizar sus pins debe repetirse la
búsqueda con el nombre de cada consumidor cuya propia versión cambió. Así se recorre la propagación
hasta alcanzar las aplicaciones finales.

```text
librería base
└── librería consumidora
    └── capacidad de scope
        └── proceso ADA
```

Cambiar un pin altera la metadata distribuida del consumidor. Por eso ese consumidor necesita al
menos un incremento `PATCH`, salvo que el cambio introduzca una capacidad o ruptura que exija un
nivel superior.

Los pins exactos son deliberados. No deben relajarse a `>=`, `~=` o rangos para evitar la
propagación: el bundler de procesos exige que la versión interna declarada coincida exactamente con
el proyecto descubierto en el monorepo.

## 7. Regenerar locks sin upgrades incidentales

El lock es derivado y no se edita manualmente. Primero se actualizan los contratos en
`pyproject.toml`; después se ejecuta `uv lock` desde el propietario correcto.

Workspace:

```bash
cd backend
uv lock --python 3.14.2 --no-python-downloads
```

Proyecto autónomo:

```bash
cd scopes/ada/processes/kpis
uv lock --python 3.14.2 --no-python-downloads
```

En `backend`, `connectivity` e `integrations`, todos los miembros del workspace comparten un único
`uv.lock`. En un proyecto autónomo solo se regenera el lock de ese proyecto.

No se utiliza `--upgrade` durante una propagación de versiones internas. Tampoco se utiliza
`--upgrade-package` salvo que la actualización de esa dependencia externa sea parte explícita del
cambio. Después de resolver se revisa el diff del lock para comprobar que no aparecieron upgrades
ajenos.

UV administra `uv.lock`; el archivo no debe corregirse a mano. Referencias oficiales:

- [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
- [Workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/)

## 8. Orden recomendado de una actualización

Una nueva versión se prepara de abajo hacia arriba.

### 1. Clasificar el cambio

Identificar el contrato afectado, consumidores conocidos, compatibilidad y nivel SemVer. La
decisión debe explicarse antes de modificar números.

### 2. Normalizar y validar el código actual

Ejemplo para una librería de backend:

```bash
cd backend
./scripts/validation/check.sh configuration --clean
```

Los validadores actuales aplican Ruff fixes y formato. Después del primer gate se debe revisar el
diff y aceptar únicamente los cambios esperados. No se incrementa una versión sobre código con
correcciones automáticas todavía sin revisar.

### 3. Actualizar la unidad propietaria

Modificar `pyproject.toml`, `__version__`, mirror, pruebas y README específico cuando corresponda.
No modificar consumidores todavía no afectados.

### 4. Propagar pins y versiones

Buscar consumidores directos, actualizar su pin y decidir el incremento de cada consumidor. Repetir
el recorrido hasta alcanzar procesos y aplicaciones finales.

### 5. Regenerar los locks afectados

Ejecutar `uv lock` desde cada workspace o proyecto autónomo propietario. Revisar cada diff y no
aceptar upgrades externos incidentales.

### 6. Ejecutar gates de abajo hacia arriba

Validar primero la librería base, después sus consumidores y finalmente los procesos. Para un
proceso ADA, el gate completo también regenera y valida su artifact:

```bash
scopes/ada/processes/kpis/scripts/check.sh --clean
```

### 7. Repetir el gate final

El último gate debe terminar sin producir nuevas modificaciones en source. Esto demuestra que
Ruff y el formato ya estaban normalizados antes de aceptar la release.

### 8. Revisar outputs

Comprobar nombres de wheels, metadata, locks, wheels internos y versión del artifact según
[Empaquetado](packaging.md).

### 9. Registrar y entregar

Solo después de una validación limpia se puede crear el registro de release o entrega soportado por
la organización. El mecanismo concreto queda pendiente hasta definir changelog, tags y destino de
publicación.

## 9. Inmutabilidad de una versión entregada

Antes de entregar, un artifact puede regenerarse varias veces con la misma versión durante su
preparación local. Una vez que un wheel o artifact fue aceptado por un receptor, su combinación de
nombre y versión queda cerrada.

Si cambia cualquiera de estos elementos, se requiere una versión nueva:

- source productivo;
- dependencia runtime o pin interno;
- `uv.lock` que altera la resolución;
- configuración de build;
- archivos incluidos en el wheel o artifact;
- entrypoint o contrato de contenedor.

No se reemplaza silenciosamente un archivo ya entregado conservando el mismo nombre y versión. Sin
esta regla no es posible reproducir, auditar ni revertir una ejecución.

## 10. Actualizar dependencias externas

Una actualización externa se realiza de manera aislada y deliberada:

1. identificar la necesidad y release objetivo;
2. revisar compatibilidad, Python soportado y cambios de seguridad;
3. actualizar el pin directo en el `pyproject.toml` propietario;
4. ejecutar `uv lock --upgrade-package <distribución>` solo en el lock correspondiente;
5. revisar dependencias transitivas modificadas;
6. ejecutar pruebas unitarias e integraciones aplicables;
7. decidir el incremento del package Atlanticus según el contrato afectado;
8. propagar versiones internas si cambió su metadata distribuida.

No se agrupan upgrades no relacionados para “dejar todo al día”. Esto reduce el alcance de una
regresión y permite identificar qué dependencia produjo el cambio.

## 11. Responsabilidad de cada README de módulo

El README de una unidad versionada debe indicar:

- versión actual del módulo;
- ruta del `pyproject.toml` propietario;
- nombre de distribución y wheel;
- import público o entrypoint;
- dependencias internas directas;
- consumidores principales conocidos;
- gate específico que certifica el módulo;
- enlace a esta guía para el procedimiento de actualización.

No debe copiar la política SemVer ni el flujo completo. Si cambia el procedimiento transversal, se
actualiza este documento y los módulos conservan únicamente sus particularidades.

## Checklist previo a una release

- [ ] El contrato afectado y sus consumidores están identificados.
- [ ] El nivel SemVer está justificado.
- [ ] Ruff y formato se aplicaron antes de modificar la versión.
- [ ] El primer gate no dejó cambios automáticos sin revisar.
- [ ] `pyproject.toml`, `__version__`, mirror y pruebas coinciden.
- [ ] Todos los pins internos apuntan a la versión real del proyecto.
- [ ] Los consumidores modificados recibieron su propio incremento.
- [ ] Solo se regeneraron los locks afectados.
- [ ] No aparecieron upgrades externos incidentales.
- [ ] Pruebas unitarias e integraciones aplicables están verdes.
- [ ] El gate final no produjo cambios nuevos.
- [ ] Wheels y artifacts fueron reconstruidos desde source.
- [ ] Ninguna entrega anterior será sobrescrita con la misma versión.
- [ ] La documentación del módulo refleja el contrato final.

## Errores frecuentes

| Síntoma | Causa probable | Corrección |
|---|---|---|
| El wheel tiene otra versión que el import | `pyproject.toml` y `__version__` divergen. | Actualizar todas las fuentes del proyecto y sus pruebas. |
| El bundler rechaza un pin interno | El consumidor conserva la versión anterior. | Propagar el pin exacto y versionar al consumidor. |
| `uv lock --check` falla | Se modificó metadata sin regenerar el lock propietario. | Ejecutar `uv lock` desde el contexto correcto. |
| Cambiaron muchas librerías externas | Se usó un upgrade amplio o se regeneró sin revisar. | Revertir el lock y repetir solo el cambio intencional. |
| Ruff modifica archivos durante el gate final | La normalización no fue revisada antes de la release. | Revisar los cambios y repetir el gate hasta quedar estable. |
| Se actualizó el package, pero no su proceso | La propagación terminó antes de alcanzar el consumidor final. | Repetir la búsqueda por distribución y regenerar artifacts. |
| Dos artifacts iguales de nombre difieren en contenido | Se reutilizó una versión ya entregada. | Incrementar la versión y reconstruir desde source. |
| Se incrementó todo Atlanticus junto | Se asumió una versión global inexistente. | Versionar solo las unidades afectadas y su cadena real. |

## Elementos no definidos o no verificados

- No existe un script oficial que actualice versiones, pins, mirrors y pruebas de forma atómica.
- No se encontró un changelog general ni changelogs por package.
- No se verificó una convención de tags Git porque el snapshot no incluye metadata del repositorio.
- No existe un pipeline de publicación ni registry Python documentado.
- No se definió un formato de release notes ni una política de soporte para versiones anteriores.
- No existe un manifiesto de checksums firmado para wheels, artifacts o distributions.
- La migración futura de packages `0.x` a `1.0.0` requiere una etapa técnica separada.

Estas ausencias no impiden usar SemVer, pero limitan la trazabilidad de una release. Deben cerrarse
antes de formalizar publicación y rollback en la guía de deployment.

## Control documental

La versión `1.0.1` corresponde exclusivamente a esta guía. No representa una versión de Atlanticus,
de sus librerías, aplicaciones, wheels, artifacts o dependencias.

---

[Volver al índice de documentación](README.md) · [Volver al README principal](../README.md)
