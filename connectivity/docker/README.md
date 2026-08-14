# Integración Docker de Atlanticus Connectivity

Connectivity mantiene dos capas de integración Docker con responsabilidades distintas.

## Integración local especializada

Cada conector que requiere un servicio externo mantiene su propio `docker/<modulo>/Dockerfile` y
`docker/<modulo>/compose.yaml`. El gate `scripts/validation/check.sh --docker` ejecuta las pruebas de
`<modulo>/tests/integration/local/`.

```text
docker/http/
docker/cosmos/
docker/service-bus/
docker/sql/
docker/storage/
docker/redis/
```

Key Vault no tiene un emulador local especializado en esta capa. Sus unit/boundary tests continúan
en el gate normal.

Los runners usan Python 3.14.2, sincronizan únicamente el paquete seleccionado y sus dependencias
reales, y copian de los demás miembros del workspace sólo la metadata mínima que UV necesita.

## Azure-local

`docker/azure-local/` contiene la infraestructura compartida para la contra-validación dentro de un
ecosistema Azure simulado. El gate es independiente:

```bash
./scripts/validation/check-azure-local.sh
./scripts/validation/check-azure-local.sh key-vault
./scripts/validation/check-azure-local.sh storage
./scripts/validation/check-azure-local.sh cosmos
./scripts/validation/check-azure-local.sh key-vault storage cosmos --clean
```

Floci-AZ se fija a una versión concreta y usa almacenamiento `memory`, de modo que cada ejecución
parte de un estado efímero. El provisioning siembra sólo valores ficticios y nunca imprime su
contenido. La adaptación de endpoint/transporte de Floci vive exclusivamente en infraestructura y
tests; los contratos productivos no conocen Floci.

Key Vault es la puerta de entrada del ambiente. Para Storage, el harness provisiona el container,
siembra su connection string ficticia en Key Vault y el test debe recuperarla mediante
`KeyVaultClient` antes de construir `StorageSettings` y ejecutar el smoke con `StorageClient`.

Para Cosmos, el harness provisiona la base y el container mediante el endpoint con sufijo que
Floci requiere para su SDK Python. Después siembra en Key Vault el endpoint raíz sin path y la key
ficticia. El test recupera ambos secretos mediante `KeyVaultClient`, construye `CosmosSettings` con
`allow_insecure_http=True`, valida `health_check()` y el contrato estructural del container mediante
`CosmosProvisioner.validate_containers()`. El contrato productivo de Cosmos sigue rechazando
endpoints con path.

El CRUD de documentos no se repite en Azure-local mientras Floci 0.10.0 no demuestre compatibilidad
con la versión `azure-cosmos` fijada por Atlanticus. La suite Python de Floci usa una versión anterior
del SDK y, con la versión de Atlanticus, las operaciones de documentos pueden quedar iterando sobre
`pkranges`. CRUD, consultas, ETag, paginación y lifecycle siguen certificados contra el emulador
oficial Cosmos en `tests/integration/local/`. El runner Azure-local impone además un timeout por suite
para que una incompatibilidad del emulador nunca bloquee indefinidamente el gate.

HTTP Client y SQL permanecen fuera de Azure-local: HTTP conserva su fake API especializada y SQL
continúa validándose contra su contenedor SQL Server.

Los futuros conectores Azure-local deben agregar sus pruebas bajo
`<modulo>/tests/integration/azure_local/` y reutilizar la misma infraestructura compartida en vez de
crear otro ecosistema independiente.

## Ejecución normal

Desde `connectivity/`:

```bash
./scripts/validation/check.sh redis
./scripts/validation/check.sh redis --docker
./scripts/validation/check.sh --docker
```

Sin `--docker`, el gate sólo ejecuta lock/sync, Ruff, unit tests, smoke imports y wheels. `--clean`
agrega limpieza de entornos y artefactos; Docker se ejecuta únicamente cuando se solicita.
