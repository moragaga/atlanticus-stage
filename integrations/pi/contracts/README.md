# Atlanticus PI Contracts

Contratos de dominio PI reutilizables por integraciones y procesos Atlanticus.

El catálogo separa la definición de cada tag de la fuente que lo entrega:

- `PiTagDefinition` describe el tag real de PI, su alias Atlanticus, tipo lógico,
  modo de extracción, materializaciones requeridas y estado activo;
- `NotPiiSource` identifica catálogos consumidos mediante NOTPII y no declara
  configuración de interpolación;
- `PiWebApiSource` identifica catálogos consumidos mediante PI Web API y declara
  una única `interpolation_seconds` compartida por todos los tags interpolados
  activos de ese catálogo;
- `PiCatalog` compone una fuente con sus definiciones.

El paquete no conoce conexiones, PI Server, WebID, endpoints, credenciales,
Service Bus, rutas de datasets, Parquet, JSON, watermarks, calendarios
operacionales ni jobs.

## Reglas

- `INTERPOLATED` puede materializar `latest`, `daily` y/o `monthly`.
- `RECORDED` sólo puede materializar `daily` y/o `monthly`.
- Un catálogo PI Web API que contenga tags interpolados activos debe declarar
  `interpolation_seconds > 0` una sola vez en su fuente.
- NOTPII no expone ni necesita `interpolation_seconds`.
- Los aliases son únicos dentro de un catálogo porque se usan como nombres de
  columnas al normalizar/pivotear los datos.
- El adapter consumidor debe exigir su fuente exacta: PI Web API no acepta
  `NotPiiSource` y NOTPII no acepta `PiWebApiSource`.
