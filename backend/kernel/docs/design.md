# Diseño inicial de Atlanticus Kernel

## Propósito

El kernel contiene conceptos que pueden ser utilizados por cualquier módulo Atlanticus sin
importar si el consumidor es un job, un motor de alarmas, un proceso de ingestión o una aplicación
Flask/Dash.

La pregunta para aceptar código dentro del kernel es:

> ¿Este comportamiento sigue teniendo sentido si eliminamos Azure, Dash, Cosmos, Redis, pandas y
> todos los conceptos de ADA?

Si la respuesta es no, el código pertenece a otro wheel.

## Qué se rescató de Foundation

La implementación anterior ya separaba correctamente cuatro ideas pequeñas:

- ambiente;
- tiempo UTC;
- estado de ejecución;
- sanitización.

Atlanticus conserva esas capacidades, pero cambia algunos comportamientos:

1. Un ambiente ausente, vacío o desconocido produce `InvalidEnvironmentError`. No se convierte
   silenciosamente en `local` ni `dev`.
2. La configuración se representa mediante la clase inmutable `Environment`.
3. La sanitización se representa mediante `DataSanitizer`, con límites configurables y seguros.
4. Los objetos desconocidos no exponen automáticamente su `repr`, porque podría contener secretos.
5. `NaN` e infinitos se convierten en texto para continuar siendo JSON válidos.

## Dependencias

El código productivo usa exclusivamente la biblioteca estándar de Python `3.14.2`.

No utiliza:

- protocolos propios;
- inyección de dependencias mediante frameworks;
- configuración global mutable;
- carga automática de `.env`;
- SDK de nube;
- logging u observabilidad.

## API pública

```python
from atlanticus.kernel import (
    DataSanitizer,
    Environment,
    EnvironmentName,
    InvalidEnvironmentError,
    KernelError,
    OperationStatus,
    utc_now,
)
```

Todo lo que no está exportado por `atlanticus.kernel.__init__` se considera detalle interno.

## Espejo comentado

La implementación productiva se refleja archivo por archivo en:

```text
commented/atlanticus/kernel/
```

El espejo conserva exactamente el código real y agrega solamente comentarios pedagógicos. La prueba
`test_commented_mirror.py` compara sus tokens de Python, ignorando comentarios y líneas no
significativas, para impedir que una de las dos versiones cambie sin la otra.

## Ambiente

`ENVIRONMENT` es la única variable que el kernel conoce. No representa la identidad de una
aplicación. Nombres como GE, IO, PR o ST deberán vivir posteriormente en `AppDefinition` dentro de
Atlanticus Web.

```python
environment = Environment.from_os()
```

El contrato admite exclusivamente:

```text
local
dev
uat
stg
prd
```

La comparación es exacta. No se normalizan mayúsculas o espacios y no existen alias. Durante el
cambio de ambientes, `uat` y `stg` pueden cumplir un propósito equivalente, pero ambos valores se
conservan porque el texto exacto se utiliza al resolver conexiones con infraestructura, incluido
Key Vault.

La ausencia de `ENVIRONMENT` también es un error. Exigir `ENVIRONMENT=local` en el computador del
desarrollador evita que un despliegue mal configurado se comporte accidentalmente como local y omita
recursos que debía resolver.

## Sanitización

`DataSanitizer` sirve para preparar datos antes de entregarlos a una capa de observabilidad. No
emite logs y no decide dónde se almacenan.

```python
sanitizer = DataSanitizer(max_depth=4, max_items=50)
safe_payload = sanitizer.sanitize(payload)
```

Los límites evitan que un diagnóstico accidental incluya listas enormes o estructuras anidadas
sin control.

Las claves se comparan ignorando mayúsculas y separadores habituales. Las excepciones conservan
únicamente su tipo: el mensaje puede incorporar credenciales, cadenas de conexión o URLs firmadas
provenientes de un SDK. El sanitizador no inspecciona texto arbitrario y, por tanto, no reemplaza la
obligación del consumidor de evitar secretos en mensajes propios.

## Evolución

No se agregarán conectores ni comportamientos de runtime al kernel. Las siguientes iteraciones
deberían crear wheels separados, comenzando por observabilidad y testing cuando se acuerden sus
contratos definitivos.
