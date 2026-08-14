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

`docker/azure-local/` contiene la infraestructura compartida para contra-validar composición dentro
de un ecosistema Azure simulado. El gate es independiente:

```bash
./scripts/validation/check-azure-local.sh
./scripts/validation/check-azure-local.sh key-vault
./scripts/validation/check-azure-local.sh storage
./scripts/validation/check-azure-local.sh cosmos
./scripts/validation/check-azure-local.sh redis
./scripts/validation/check-azure-local.sh key-vault storage cosmos redis --clean
```

Floci-AZ se fija a `0.10.0` y usa almacenamiento `memory`, de modo que cada ejecución parte de un
estado efímero. El provisioning siembra sólo valores ficticios y nunca imprime su contenido. La
adaptación de endpoint/transporte de Floci vive exclusivamente en infraestructura y tests; los
contratos productivos no conocen Floci.

Key Vault es la puerta de entrada del ambiente. Para Storage, el harness provisiona el container,
siembra su connection string ficticia en Key Vault y el test la recupera mediante `KeyVaultClient`
antes de construir `StorageSettings` y operar con `StorageClient`.

Para Cosmos, el harness provisiona database/container por REST específico de Floci. Después siembra
endpoint y key ficticia en Key Vault. El test recupera ambos mediante `KeyVaultClient` y usa el
`CosmosClient` real con `azure-cosmos==4.16.3` para validar health y contrato de container. CRUD,
queries, ETag y paginación continúan certificados en el emulador oficial de Cosmos bajo la
integración especializada.

Para Redis, Floci necesita acceso al Docker socket porque crea un sidecar Valkey real. El harness
provisiona el cache mediante el plano ARM de Floci, espera `provisioningState=Succeeded` y guarda en
Key Vault URL, username y password ficticios. El test recupera esos valores con `KeyVaultClient`,
construye `RedisSettings` con `allow_insecure_transport=True` y usa el `RedisClient` productivo para
PING, SET/GET, EXISTS, MGET, TTL/EXPIRE y DELETE. El sidecar comparte una red Docker explícita con el
runner para que el hostname retornado por Floci sea resolvible sin publicar credenciales ni adaptar
el cliente productivo.

HTTP Client, SQL y Service Bus permanecen fuera de Azure-local. HTTP conserva su fake API; SQL se
valida contra SQL Server; Service Bus conserva el emulador oficial de Microsoft. En Floci 0.10.0 el
Service Bus data plane permanece deliberadamente mocked por una incompatibilidad AMQP upstream, por
lo que no se introduce un workaround Atlanticus para habilitarlo.

Los futuros conectores Azure-local deben agregar sus pruebas bajo
`<modulo>/tests/integration/azure_local/` y reutilizar esta infraestructura compartida.

## Ejecución normal

Desde `connectivity/`:

```bash
./scripts/validation/check.sh redis
./scripts/validation/check.sh redis --docker
./scripts/validation/check.sh --docker
```

Sin `--docker`, el gate sólo ejecuta lock/sync, Ruff, unit tests, smoke imports y wheels. `--clean`
agrega limpieza de entornos y artefactos; Docker se ejecuta únicamente cuando se solicita.
