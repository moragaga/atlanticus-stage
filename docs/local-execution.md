<p align="right">
  <img src="assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Ejecución local

[Volver al índice de documentación](README.md) · [Volver al README principal](../README.md)

Esta guía explica cómo ejecutar aplicaciones backend de Atlanticus desde su código fuente, desde
un artifact transportable y mediante Docker. El objetivo es elegir el nivel de aislamiento
adecuado sin convertir Docker en un requisito para cada prueba local.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.0.2` |
| Estado | Validado |
| Python requerido | `3.14.2` |
| Gestor de proyectos | UV |
| Ejemplo transversal | Proceso ADA `kpis` |
| Audiencia | Desarrollo, soporte y operación local |

## Alcance

Esta guía es la fuente de verdad para:

- elegir entre ejecución desde source, artifact o contenedor;
- sincronizar lo necesario antes de ejecutar;
- utilizar el entrypoint declarado por una aplicación;
- ejecutar una iteración o iniciar el ciclo continuo;
- preparar y operar el workspace Docker Compose local;
- localizar logs, datasets, state y leases dentro del volumen;
- detener cada modalidad de forma controlada;
- reconocer los errores locales más frecuentes.

No define todas las variables de cada aplicación, el significado funcional de una iteración, la
construcción interna de wheels, la política de versiones ni un deployment productivo. Esas
responsabilidades pertenecen a sus guías transversales o al README de la aplicación.

## Qué se ejecuta

Atlanticus contiene librerías y aplicaciones. No deben tratarse como si fueran lo mismo.

| Tipo | Ejemplo | Uso esperado |
|---|---|---|
| Librería | `atlanticus-kernel` | Se importa desde otra librería o aplicación; no se levanta como proceso. |
| Aplicación backend | `ada-kpis-process` | Declara un entrypoint y puede ejecutarse desde source o artifact. |
| Imagen de proceso | `atlanticus-kpis:local` | Encapsula un artifact y lo ejecuta con el mismo entrypoint. |
| Workspace Compose | `atlanticus-local` | Construye y administra conjuntamente los artifacts preparados. |

El comando ejecutable oficial se declara en `[project.scripts]` dentro del `pyproject.toml`. Para
`kpis`, el contrato es:

```toml
[project.scripts]
ada-kpis = "ada.processes.kpis.bootstrap:main"
```

El nombre del package, su import de Python y su comando pueden ser diferentes. Para ejecutar una
aplicación debe usarse el comando declarado, no deducirlo a partir del nombre del directorio.

## Elegir la modalidad

| Necesidad | Modalidad recomendada | Qué valida |
|---|---|---|
| Cambiar código y obtener retroalimentación rápida | Source con UV | Código editable, configuración y comportamiento. |
| Probar exactamente el bundle entregable sin Docker | Artifact con UV | Lock transportable, wheels internos y entrypoint. |
| Validar la imagen y su aislamiento | Docker directo | Dockerfile, instalación runtime, variables y volumen. |
| Probar varios artifacts como conjunto | Orquestador local | Generación Compose, imágenes, servicios y volumen compartido. |

Las modalidades no compiten entre sí. Representan niveles progresivos de aislamiento y responden
preguntas distintas.

## Prerrequisitos

Antes de ejecutar desde source o artifact deben estar instalados UV y Python `3.14.2`, según
[Primeros pasos y desarrollo](development.md).

Docker solo es obligatorio para las modalidades de contenedor. El orquestador requiere además el
plugin `docker compose`.

Verificaciones mínimas:

```bash
uv --version
uv python find 3.14.2
docker --version
docker compose version
```

Las dos verificaciones Docker pueden omitirse cuando la ejecución será únicamente con UV.

## Configuración local mínima

Cada proceso contiene `.env.detail` como referencia versionada. El archivo ejecutable `.env` debe
crearse localmente y no debe publicarse ni copiarse a una entrega.

Desde la raíz del proceso elegido:

```bash
cp .env.detail .env
```

Después deben reemplazarse placeholders y valores que dependan del ambiente. La guía transversal
de configuración documentará el contrato completo; el README de cada proceso explicará sus
variables exclusivas.

Todas las aplicaciones soportadas comparten al menos estas identidades operacionales:

| Variable | Responsabilidad |
|---|---|
| `ENVIRONMENT` | Selecciona el ambiente lógico. Para desarrollo se utiliza `local`. |
| `APPLICATION` | Identifica el espacio lógico donde una aplicación organiza sus outputs, logs y state. |
| `VOLUMEN_PATH` | Define la raíz física disponible para Atlanticus. Debe ser una ruta absoluta del ambiente de ejecución. |
| `ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED` | Habilita o deshabilita logs persistidos en archivos. |

La raíz funcional se resuelve como:

```text
<VOLUMEN_PATH>/<APPLICATION>/
```

En una ejecución local con UV, `VOLUMEN_PATH` debe apuntar a una ubicación absoluta del equipo
donde se desea conservar la información. Por ejemplo:

```dotenv
VOLUMEN_PATH=/home/usuario/atlanticus/runtime
```

En Windows también puede expresarse como una ruta absoluta compatible con Python:

```dotenv
VOLUMEN_PATH=D:/atlanticus/runtime
```

Si varios procesos usan exactamente el mismo `VOLUMEN_PATH` y el mismo `APPLICATION`, todos
resuelven la misma raíz de aplicación. Esto permite que una composición integrada comparta sus
datasets, logs y state bajo una identidad común. Si cambia cualquiera de los dos valores, la raíz
resultante es diferente.

Compartir `APPLICATION` debe ser una decisión arquitectónica deliberada. Los procesos involucrados
deben utilizar namespaces, servicios y contratos de datasets compatibles para no sobrescribir
información ajena. Las ejecuciones que necesitan aislamiento deben utilizar otro `APPLICATION`,
aunque compartan el mismo `VOLUMEN_PATH`.

## 1. Ejecutar desde source con UV

Esta es la modalidad principal durante desarrollo. Las dependencias internas se resuelven desde
las rutas editables declaradas en `[tool.uv.sources]`.

### Entrar al proyecto

Desde la raíz del repositorio:

```bash
cd scopes/ada/processes/kpis
```

### Preparar el entorno

Crear y configurar `.env`:

```bash
cp .env.detail .env
```

Sincronizar el proyecto la primera vez o cuando cambien `pyproject.toml` o `uv.lock`:

```bash
uv sync --group dev --frozen
```

Este es el comando cotidiano. Las opciones estrictas adicionales pertenecen a los validadores
oficiales y no necesitan repetirse en cada ejecución manual.

### Ejecutar una sola iteración

```bash
uv run --frozen ada-kpis --run-once
```

`--run-once` solicita al runtime cerrar después de completar una iteración. El trabajo exacto que
representa esa iteración depende del proceso y se documenta en su README.

### Ejecutar el ciclo continuo

```bash
uv run --frozen ada-kpis
```

Sin `--run-once`, el runtime continúa evaluando iteraciones conforme a la definición del job hasta
que alcanza una condición de cierre o recibe una interrupción.

Detenerlo desde la misma terminal:

```text
Ctrl+C
```

El runtime solicita un cierre controlado y libera sus recursos antes de terminar.

### Ejecutar como módulo de Python

Los procesos actuales también incluyen `__main__.py`. Por eso la forma equivalente es:

```bash
uv run --frozen python -m ada.processes.kpis --run-once
```

El entrypoint `ada-kpis` sigue siendo la forma recomendada porque es el contrato compartido por
UV, el artifact y Docker.

### Opciones comunes del runtime

| Opción | Efecto |
|---|---|
| Sin opción | Ejecuta el ciclo normal del job. |
| `--run-once` | Finaliza después de una iteración. |
| `--debug` | Fuerza una única iteración; actualmente no habilita un logger diferente. |
| `--environment <valor>` | Declara el ambiente desde CLI; debe coincidir con `ENVIRONMENT` si ambos existen. |

Los argumentos desconocidos se rechazan. Los tiempos de espera, leases y límites de cada proceso
pertenecen a su definición de job, no a argumentos libres de línea de comandos.

## 2. Ejecutar un artifact con UV

Un artifact de proceso es un proyecto Python autónomo. Incluye su source ejecutable, `uv.lock` y
los wheels de todas sus dependencias internas. Permite validar la entrega sin construir una imagen.

### Preparar el artifact

Desde la raíz del repositorio:

```bash
./scripts/local-process.sh prepare kpis
```

El resultado se crea en:

```text
artifacts/processes/kpis/
```

La construcción y validación interna del artifact pertenece a la guía de empaquetado. En esta guía
solo se utiliza el resultado preparado.

### Configurarlo

```bash
cd artifacts/processes/kpis
cp .env.detail .env
```

Completar `.env` antes de sincronizar o ejecutar. El archivo no está incluido deliberadamente por
el bundler.

### Crear su entorno autónomo

```bash
uv sync --frozen
```

El `pyproject.toml` transportable deshabilita grupos por defecto y reemplaza las rutas editables
por wheels bajo `wheels/`. Esta sincronización instala únicamente la aplicación y sus dependencias
runtime fijadas por el lock.

### Ejecutarlo una vez

```bash
uv run --frozen ada-kpis --run-once
```

### Ejecutarlo continuamente

```bash
uv run --frozen ada-kpis
```

La configuración y los outputs son los mismos que en source. La diferencia es el origen de las
dependencias: source editable durante desarrollo y wheels internos dentro del artifact.

## 3. Ejecutar un artifact con Docker

Esta modalidad valida el contrato de contenedor de un único artifact. Los comandos siguientes se
ejecutan desde la raíz del repositorio, después de preparar y configurar
`artifacts/processes/kpis/.env`.

### Construir la imagen

```bash
docker build \
  --file deployment/processes/Dockerfile \
  --build-arg FILENAME=kpis \
  --tag atlanticus-kpis:local \
  artifacts
```

El contexto debe ser `artifacts/` porque el Dockerfile copia `processes/<FILENAME>/`. Durante la
construcción se instala el artifact con su lock y se verifica que el comando declarado exista.

### Ejecutar una sola iteración

```bash
docker run --rm \
  --env-file artifacts/processes/kpis/.env \
  --env VOLUMEN_PATH=/app/volume \
  --mount type=volume,source=atlanticus-runtime,target=/app/volume \
  atlanticus-kpis:local \
  --run-once
```

`VOLUMEN_PATH` se reemplaza dentro del contenedor porque la ruta absoluta del host no existe dentro
de la imagen. En este ejemplo, Docker monta el volumen persistente `atlanticus-runtime` en
`/app/volume`; por eso el proceso recibe esa ruta como `VOLUMEN_PATH`. La salida de consola aparece
directamente en la terminal.

Este reemplazo no modifica la identidad lógica de la aplicación. El valor de `APPLICATION` sigue
determinando el subdirectorio utilizado dentro del volumen montado.

### Ejecutar continuamente

El mismo contenedor inicia el ciclo normal al retirar `--run-once`:

```bash
docker run --rm \
  --env-file artifacts/processes/kpis/.env \
  --env VOLUMEN_PATH=/app/volume \
  --mount type=volume,source=atlanticus-runtime,target=/app/volume \
  atlanticus-kpis:local
```

Se detiene con `Ctrl+C`. Docker transmite la interrupción al entrypoint y el runtime realiza su
cierre controlado.

## 4. Ejecutar artifacts con el orquestador local

`scripts/local-process.sh` administra un workspace Docker Compose generado por Atlanticus. No es
el mecanismo más rápido para probar un cambio de código; está orientado a validar uno o varios
artifacts como contenedores coordinados.

### Preparar los procesos

Preparar uno o varios:

```bash
./scripts/local-process.sh prepare kpis kpis-historian
```

Preparar todos los procesos exportables:

```bash
./scripts/local-process.sh prepare --all
```

Cada artifact preparado necesita su propio `.env`:

```bash
cp artifacts/processes/kpis/.env.detail artifacts/processes/kpis/.env
cp artifacts/processes/kpis-historian/.env.detail artifacts/processes/kpis-historian/.env
```

El comando `up` valida todos los artifacts detectados. Si uno carece de `.env`, el conjunto no se
levanta.

### Levantar el conjunto

```bash
./scripts/local-process.sh up
```

Este comando:

1. valida UV, Docker y los artifacts;
2. genera `.runtime/local-deployment/compose.yaml`;
3. construye todas las imágenes sin cache;
4. levanta los servicios en segundo plano con `--run-once`;
5. muestra también los contenedores que ya finalizaron.

El volumen predeterminado es un volumen nombrado de Docker. Para inspeccionar los outputs como
archivos locales puede utilizarse un bind mount:

```bash
./scripts/local-process.sh up --bind
```

En esa modalidad los datos quedan bajo:

```text
.runtime/local-deployment/runtime/
```

### Consultar estado y logs

```bash
./scripts/local-process.sh ps
./scripts/local-process.sh logs kpis
```

Sin nombre de proceso, `logs` sigue la salida de todos los servicios:

```bash
./scripts/local-process.sh logs
```

La opción `-f` aplicada internamente mantiene el seguimiento activo. Se abandona la vista con
`Ctrl+C`; esto no detiene los contenedores.

### Repetir una ejecución aislada

```bash
./scripts/local-process.sh run kpis
```

El orquestador ejecuta `docker compose run --rm kpis --run-once`. El servicio debe existir en el
Compose ya generado.

### Detener el conjunto

```bash
./scripts/local-process.sh down
```

Se retiran contenedores y recursos huérfanos del proyecto Compose. El volumen nombrado no se
elimina porque `down` no utiliza `--volumes`.

## Outputs locales

Para ejecuciones con UV, las rutas se resuelven directamente desde `VOLUMEN_PATH` y `APPLICATION`.
En Docker, la misma estructura se crea dentro del volumen montado.

| Contenido | Ruta base |
|---|---|
| Raíz de la aplicación | `<VOLUMEN_PATH>/<APPLICATION>/` |
| Logs persistidos | `<VOLUMEN_PATH>/<APPLICATION>/logs/` |
| Datasets | `<VOLUMEN_PATH>/<APPLICATION>/datasets/` |
| State | `<VOLUMEN_PATH>/<APPLICATION>/.runtime/state/` |
| Leases | `<VOLUMEN_PATH>/<APPLICATION>/.runtime/leases/` |

La ubicación física cambia entre ambientes, pero el contrato no cambia:

| Ambiente | `VOLUMEN_PATH` representa |
|---|---|
| UV local | Una ruta absoluta del equipo del desarrollador. |
| Docker local | El destino interno de un bind mount o volumen nombrado. |
| Deployment | El punto de montaje interno asignado al almacenamiento persistente del contenedor. |

La ruta local del desarrollador no se incorpora al artifact ni a la imagen. En un deployment, la
plataforma monta el almacenamiento persistente configurado para el contenedor y entrega su punto de
montaje como `VOLUMEN_PATH`. Por eso cambiar la ruta usada en desarrollo no altera ni reemplaza el
volumen existente del ambiente desplegado.

No todos los procesos escriben en todas las rutas. El README de cada aplicación debe identificar
sus datasets, state y outputs funcionales concretos.

Los logs también se emiten a la consola. Si
`ATLANTICUS_OBSERVABILITY_FILE_LOGS_ENABLED=false`, la salida de consola permanece disponible, pero
no se crea la persistencia de archivos.

## Reiniciar una prueba desde cero

`clean_root.sh` elimina estado generado del repositorio, incluidos entornos, caches, artifacts,
distributions y runtime local. No elimina el código fuente.

Antes de usarlo, revisar el alcance:

```bash
./clean_root.sh --dry-run
```

Cuando realmente se requiera una validación limpia:

```bash
./clean_root.sh
```

No debe utilizarse como paso rutinario para volver a ejecutar un único proceso. Si se necesita
conservar evidencia, logs o datasets, deben respaldarse antes de la limpieza.

## Errores frecuentes

| Síntoma | Causa probable | Revisión |
|---|---|---|
| `Required command not found: uv` | UV no está instalado o no está en `PATH`. | Seguir la guía de desarrollo. |
| Python `3.14.2` no aparece | La versión requerida no está instalada. | Ejecutar `uv python install 3.14.2`. |
| Falta `.env` | Solo existe la plantilla `.env.detail`. | Copiarla, completar valores y no versionarla. |
| `VOLUMEN_PATH must be an absolute path` | Se configuró una ruta relativa. | Usar una ruta absoluta del host o `/app/volume` en Docker. |
| El comando de la aplicación no existe | El proyecto no fue sincronizado o se usó un nombre deducido. | Ejecutar `uv sync --frozen` y revisar `[project.scripts]`. |
| UV reporta un lock desactualizado | `pyproject.toml` cambió sin actualizar `uv.lock`. | Corregir el lock dentro del flujo de desarrollo; no retirar `--frozen`. |
| Falta un servicio Compose | El artifact no estaba preparado cuando se generó el workspace. | Prepararlo y volver a ejecutar `up`. |
| Un proceso termina sin producir datos | La infraestructura, fuentes o catálogos no están disponibles. | Revisar el README del proceso y sus dependencias operacionales. |

## Límites de responsabilidad

| Pregunta | Documento propietario |
|---|---|
| ¿Cómo instalo UV, Python y preparo una `.venv`? | [Primeros pasos y desarrollo](development.md) |
| ¿Qué significa cada variable o archivo de secretos? | `docs/configuration.md` |
| ¿Cómo se construyen wheels y artifacts? | `docs/packaging.md` |
| ¿Cómo se genera una distribución o deployment? | `docs/deployment.md` |
| ¿Qué hace una iteración de `kpis`, `dispatch` u otro proceso? | README del proceso correspondiente |
| ¿Cómo se ejecuta una aplicación ya identificada? | Esta guía |

Los enlaces pendientes se activarán cuando cada documento haya sido validado. La documentación BAT
se incorporará cuando sus equivalencias reales estén contrastadas; esta revisión describe los
flujos Bash disponibles.

## Control documental

La versión `1.0.2` corresponde exclusivamente a esta guía. No representa una versión de Atlanticus,
de sus procesos, wheels, artifacts ni imágenes.

---

[Volver al índice de documentación](README.md) · [Volver al README principal](../README.md)
