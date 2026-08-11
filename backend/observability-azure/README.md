# Atlanticus Observability Azure

`atlanticus-observability-azure==0.1.0` extiende la observabilidad neutral con una proyección
deliberadamente acotada para Azure Monitor. El SDK de Azure vive sólo en este wheel.

## Modos y perfiles

```text
ATLANTICUS_AZURE_OBSERVABILITY_MODE=off|preview|export
ATLANTICUS_AZURE_OBSERVABILITY_PROFILE=slim|diagnostic
APPLICATION_INSIGHTS_CONNECTION_STRING=<secret>
```

- `off` no carga ni configura Azure;
- `preview` escribe `azure-preview.jsonl` junto a la traza diaria para simular la salida Azure, sin
  red;
- `export` emite cada evento operacional como JSON compacto en el cuerpo del log de Application
  Insights;
- `slim` conserva eventos operacionales, métricas, warnings y errores con detalle sanitizado;
- `diagnostic` conserva exactamente esos eventos y agrega sólo spans fallidos o lentos, con contexto
  técnico seleccionado.

El perfil predeterminado es `slim` y el modo predeterminado es `off`. No hay muestreo aleatorio de
eventos operacionales: cada resultado explícitamente relevante se envía. El perfil `diagnostic`
debe habilitarse sólo durante una investigación.

La extensión no activa autoinstrumentación, métricas de proceso, live metrics ni almacenamiento
offline del exporter. Las cantidades de negocio viajan como campos del JSON operacional. Para
evitar costo y alta cardinalidad, Azure indexa sólo `event`, `application`, `environment`, `service`
y `run_id`; soporte puede recuperar el contenido completo con `parse_json(message)`.

Los perfiles afectan únicamente la exportación Azure y su preview. No modifican
`executions.jsonl`, `iterations.jsonl`, `issues.jsonl`, los resúmenes ni el estado `latest` que
mantiene `atlanticus-observability`.

Los spans de `diagnostic` se restringen a fallos y duraciones de al menos dos segundos. Sólo admiten
`component` y `source`, ambos acotados, además de la identidad de ejecución y el error sanitizado.

## Evento operacional

```python
from atlanticus.observability import EventAudience, emit_data_event

emit_data_event(
    'data.downloaded',
    audience=EventAudience.OPERATIONS,
    record_count=1250,
    file_count=2,
    attributes={'minimum_timestamp': '2026-07-20T00:00:00Z'},
)
```

El detalle específico de KPI, alarmas, ingestas, backups y delivery se incorporará en sus
contratos de dominio. Observability sólo define el mecanismo común.
