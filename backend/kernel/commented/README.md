# Espejo comentado de Atlanticus Kernel

Esta ruta contiene un espejo completo del código productivo del kernel. Conserva los mismos
archivos, instrucciones, nombres, tipos, valores y comportamiento; la única diferencia permitida
es la incorporación de comentarios explicativos.

```text
Código productivo
src/atlanticus/kernel/

Espejo comentado
commented/atlanticus/kernel/
```

## Objetivo

El código productivo permite leer rápidamente la implementación real. El espejo explica esa misma
implementación paso a paso para un desarrollador que todavía no conoce el proyecto.

El espejo no es una versión resumida, un ejemplo ni una reimplementación. Tampoco forma parte del
wheel.

## Archivos reflejados

```text
__init__.py
environment.py
errors.py
sanitization.py
status.py
time.py
```

## Garantía de equivalencia

La prueba `tests/test_commented_mirror.py` compara los tokens de Python de ambas rutas ignorando
solamente comentarios y líneas no significativas. La validación falla si:

- falta un archivo en cualquiera de las dos rutas;
- se modifica una instrucción solamente en producción;
- el espejo cambia un valor, tipo, nombre o comportamiento;
- se agrega lógica al espejo que no existe en producción.

Cuando cambie el kernel se debe actualizar primero el código productivo, después copiar exactamente
el cambio al espejo y finalmente agregar los comentarios que expliquen la decisión.
