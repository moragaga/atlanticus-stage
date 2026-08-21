<p align="right">
  <img src="assets/atlanticus-isotype.png" alt="Atlanticus" width="260">
</p>

# Documentación de Atlanticus

[Volver al README principal](../README.md)

Este documento es el índice general de la documentación técnica de Atlanticus. Permite identificar
dónde se explica cada tema sin duplicar procedimientos entre capas, módulos y aplicaciones.

| Referencia | Valor |
|---|---|
| Versión del documento | `1.10.0` |
| Estado | En revisión |
| Audiencia | Desarrollo, arquitectura, soporte y plataforma |
| Documento superior | `README.md` |

## Propósito

El directorio `docs/` contiene las guías transversales compartidas por todo el ecosistema. Estas
guías explican los procedimientos comunes de desarrollo, configuración, ejecución, construcción,
versionamiento y deployment.

Este índice:

- orienta a cada persona hacia la guía que necesita;
- diferencia documentación transversal y documentación específica;
- identifica qué documentos están validados y cuáles siguen pendientes;
- evita mantener el mismo procedimiento en múltiples README;
- establece una única fuente de verdad para cada responsabilidad documental.

No contiene código productivo ni reemplaza los contratos técnicos declarados por los módulos.

## Niveles de documentación

```text
README principal
└── docs/README.md
    ├── guías transversales
    ├── README de áreas
    └── README de módulos y aplicaciones
```

| Nivel | Pregunta que responde |
|---|---|
| README principal | ¿Qué es Atlanticus y cómo está organizado? |
| Índice general | ¿Dónde encuentro la información que necesito? |
| Guía transversal | ¿Cómo se realiza un procedimiento compartido? |
| README de área | ¿Qué contiene una capa y cómo se relacionan sus módulos? |
| README de módulo | ¿Qué hace un componente y cuáles son sus particularidades? |
| README de aplicación | ¿Qué ejecuta la aplicación y qué significa su operación? |

## Guías transversales

Las rutas se convertirán en enlaces cuando cada documento haya sido creado y validado.

| Guía | Ruta prevista | Responsabilidad | Estado |
|---|---|---|---|
| [Primeros pasos y desarrollo](development.md) | `docs/development.md` | Instalación de UV y Python, entorno, sincronización, `.venv`, proyectos, nombres, Ruff y pruebas. | Validado |
| [Arquitectura](architecture.md) | `docs/architecture.md` | Capas, contratos, dependencias permitidas, composición y límites. | Validado |
| [Configuración](configuration.md) | `docs/configuration.md` | Variables de entorno, archivos de configuración, secretos y resolución por ambiente. | Validado |
| [Ejecución local](local-execution.md) | `docs/local-execution.md` | Ejecución desde source o artifact mediante UV, Docker y orquestadores. | Validado |
| [Empaquetado](packaging.md) | `docs/packaging.md` | Construcción, inspección y validación de wheels y artifacts. | Validado |
| [Versionamiento](versioning.md) | `docs/versioning.md` | SemVer, actualización de dependencias y gate previo a una publicación. | Validado |
| [Deployment](deployment.md) | `docs/deployment.md` | Distribuciones, contenedores y mecanismos de despliegue soportados. | Validado |

## Recorridos recomendados

No todas las personas necesitan leer toda la documentación. Estos recorridos indican el orden
esperado según la responsabilidad.

| Perfil | Recorrido recomendado |
|---|---|
| Persona nueva | README principal → Primeros pasos → Arquitectura → README del área de trabajo |
| Desarrollo de módulos | Primeros pasos → Configuración → README del módulo → Versionamiento |
| Mantenimiento técnico | Arquitectura → Versionamiento → Empaquetado |
| Operación local | Configuración → Ejecución local → README de la aplicación |
| Equipo de plataforma | Empaquetado → Deployment → README de la aplicación |
| Desarrollo de ADA | README de ADA → capacidad o proceso correspondiente → guía transversal necesaria |

## Documentación por área

Los índices de área describirán responsabilidades, límites, módulos contenidos y relaciones. No
repetirán procedimientos que pertenezcan a las guías transversales.

| Área | Ruta prevista | Estado documental |
|---|---|---|
| Backend | `backend/README.md` | En revisión |
| Connectivity | `connectivity/README.md` | Pendiente de revisión |
| Integrations | `integrations/README.md` | Pendiente de revisión |
| Scopes | `scopes/README.md` | Pendiente |
| Data Producers | `scopes/data-producers/README.md` | Pendiente |
| ADA | `scopes/ada/README.md` | Pendiente |
| Deployment | `deployment/README.md` | Pendiente |
| Scripts | `scripts/README.md` | Pendiente |

Un archivo existente no se considera validado únicamente por estar presente en el repositorio. Se
incorporará como enlace cuando su contenido haya sido contrastado con el código y aprobado dentro
de esta estructura documental.

## Fuente de verdad

Cada tema debe tener un único documento propietario:

| Tema | Documento propietario |
|---|---|
| Instalación y preparación del entorno | Primeros pasos y desarrollo |
| Dependencias entre capas y contratos | Arquitectura |
| Variables, configuración y secretos | Configuración |
| UV, Docker, logs, state y limpieza local | Ejecución local |
| Wheels y artifacts | Empaquetado |
| SemVer, Ruff y publicación de versiones | Versionamiento |
| Distribuciones y despliegues | Deployment |
| Variables y comportamiento exclusivos de un módulo | README del módulo |
| Significado de una ejecución o iteración | README de la aplicación |

Si un procedimiento común cambia, se actualiza su guía propietaria. Los README consumidores solo
mantienen el enlace y sus diferencias específicas.

## Estados documentales

| Estado | Significado |
|---|---|
| Pendiente | El documento todavía no existe. |
| Pendiente de revisión | Existe contenido previo, pero aún no ha sido validado contra la arquitectura actual. |
| En revisión | Se creó una propuesta y espera aprobación. |
| Validado | El contenido fue contrastado con el repositorio y aprobado. |
| Verificación externa pendiente | La estructura está validada, pero requiere infraestructura o credenciales reales. |

## Convenciones

- Todos los documentos se redactan en español.
- Los mensajes de error y contratos técnicos conservan el inglés utilizado por el código.
- Las rutas, nombres, variables y comandos deben coincidir con el repositorio.
- No se documentan valores secretos ni credenciales reales.
- No se inventan equivalencias entre Bash, BAT, UV, Docker o Azure.
- Los outputs generados no se presentan como fuente del código.
- Las instrucciones genéricas no se copian en los README de módulos.
- Cada documento mantiene su propia versión documental.
- Git conserva el historial y la versión documental identifica su revisión funcional.

## Control documental

La versión `1.10.0` corresponde exclusivamente a este índice. No representa una versión global de
Atlanticus ni modifica las versiones de sus librerías, aplicaciones, wheels o artifacts.

Las guías **Primeros pasos y desarrollo**, **Ejecución local**, **Arquitectura** y
**Configuración**, **Empaquetado**, **Versionamiento** y **Deployment** se encuentran validadas.
El índice de área **Backend** es la siguiente propuesta en revisión; su ruta se transformará en
enlace cuando quede aprobada.

---

[Volver al README principal](../README.md)
