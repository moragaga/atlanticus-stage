# Integración Docker de Atlanticus Connectivity

Las integraciones de Connectivity son modulares. Cada conector certificado que requiere servicios
externos mantiene su propio `docker/<modulo>/Dockerfile` y `docker/<modulo>/compose.yaml`; el gate
`connectivity/scripts/validation/check.sh` decide qué integración levantar con `--docker`.

## Módulos actuales

```text
docker/http/
docker/cosmos/
docker/service-bus/
docker/sql/
```

Key Vault no requiere una integración Docker propia en esta etapa.

Los runners usan `python:3.14.2-slim-bookworm`, sincronizan únicamente el paquete seleccionado y sus
dependencias reales, y copian de los otros miembros del workspace sólo el `pyproject.toml` necesario
para que UV resuelva el workspace. Los tests productivos se copian únicamente desde el módulo que se
está validando.

## SQL

SQL usa `mssql-python==1.13.0`. El runner instala sólo `libltdl7`, `libkrb5-3` y
`libgssapi-krb5-2`; no instala `unixODBC`, `msodbcsql17` ni `msodbcsql18`. El contenedor
`sql-integration` no fija plataforma y puede ejecutarse nativamente en Linux ARM64 en macOS Apple
Silicon. Sólo `sql-server` conserva `platform: linux/amd64` porque la imagen oficial de SQL Server
2019 es x86-64.

La aceptación valida connection strings sin Driver y secretos legacy con ODBC Driver 17/18,
lecturas acotadas y por lotes, parámetros, tipos, timeout, errores sanitizados, permisos de sólo
lectura, `UNIQUEIDENTIFIER` compatible y tres conexiones concurrentes independientes.

## Ejecución

Desde `connectivity/`:

```bash
./scripts/validation/check.sh sql
./scripts/validation/check.sh sql --docker
./scripts/validation/check.sh --docker
```

Sin `--docker`, el gate sólo ejecuta lock/sync, Ruff, unit tests, smoke imports y wheels. `--clean`
agrega limpieza de entornos y artefactos; Docker se ejecuta únicamente cuando se solicita de forma
explícita. Cada integración elimina contenedores, volúmenes e imagen del runner al terminar.
