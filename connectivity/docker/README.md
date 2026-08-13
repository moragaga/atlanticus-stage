# Integración Docker de Atlanticus Connectivity

Las integraciones de Connectivity son modulares. Cada conector que requiere servicios externos mantiene su propio `docker/<modulo>/Dockerfile` y `docker/<modulo>/compose.yaml`; el gate `connectivity/scripts/validation/check.sh` decide qué integración levantar con `--docker`.

## Módulos actuales

```text
docker/http/
docker/cosmos/
docker/service-bus/
docker/sql/
docker/storage/
docker/redis/
```

Key Vault no requiere una integración Docker propia en esta etapa.

Los runners usan `python:3.14.2-slim-bookworm`, sincronizan únicamente el paquete seleccionado y sus dependencias reales, y copian de los otros miembros del workspace sólo el `pyproject.toml` necesario para que UV resuelva el workspace. Los tests productivos se copian únicamente desde el módulo validado.

## SQL

SQL usa `mssql-python==1.13.0`. El runner no instala `unixODBC`, `msodbcsql17` ni `msodbcsql18`. El contenedor cliente no fija plataforma; sólo SQL Server local mantiene `linux/amd64` por la arquitectura de su imagen.

## Storage

Storage se valida contra Azurite y cubre connection string, SAS, CRUD, streams, metadata y listados por prefix.

## Redis

Redis se valida contra una imagen oficial versionada. El servidor local usa password y transporte sin TLS únicamente dentro de la red Docker; el cliente exige `allow_insecure_transport=True` para ese escenario. El runner no fija plataforma.

## Ejecución

Desde `connectivity/`:

```bash
./scripts/validation/check.sh redis
./scripts/validation/check.sh redis --docker
./scripts/validation/check.sh --docker
```

Sin `--docker`, el gate sólo ejecuta lock/sync, Ruff, unit tests, smoke imports y wheels. `--clean` agrega limpieza de entornos y artefactos; Docker se ejecuta únicamente cuando se solicita explícitamente. Cada integración elimina contenedores, volúmenes e imagen del runner al terminar.
