# Atlanticus Integrations

`integrations` contiene semántica reusable para sistemas externos. No reemplaza a `connectivity`:

- `connectivity`: transporte y clientes tecnológicos genéricos;
- `integrations`: contratos y adapters que entienden un sistema externo;
- `processes`: jobs ejecutables, planificación, materialización y persistencia;
- `scopes`: composición de soluciones como ADA.

## PI

La primera integración es `pi/contracts` (`atlanticus-pi-contracts==0.1.0`).

Por diseño todavía no existen aquí:

- un catálogo concreto de tags;
- PI Web API;
- NOTPII;
- materializaciones `latest`, `daily` o `monthly`.

### Primer lock del workspace

Como `integrations` es un workspace nuevo, crear una vez su lock con Python 3.14.2:

```bash
cd integrations
uv lock --python 3.14.2 --no-python-downloads
```

Luego el gate normal queda congelado:

```bash
./scripts/validation/check.sh --clean
```
