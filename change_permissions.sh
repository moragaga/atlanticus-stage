#!/usr/bin/env bash
set -euo pipefail

RUTA="$(pwd)"
SCRIPT_PATH="$(realpath "$0")"

echo "Atlanticus: assigning privileges in $RUTA"

# Asegurar que el usuario actual sea propietario.
sudo chown -R "$USER":"$(id -gn)" "$RUTA"

# Carpetas: lectura, escritura, acceso y herencia del grupo.
sudo find "$RUTA" -type d -exec chmod 2775 {} +

# Archivos: agregar lectura y escritura sin eliminar ejecución existente.
sudo find "$RUTA" -type f -exec chmod ug+rw,o+r {} +

# Dar ejecución a scripts de shell.
sudo find "$RUTA" -type f \( \
    -name "*.sh" -o \
    -name "*.bash" \
\) -exec chmod ug+x {} +

# Asegurar que este mismo script siga siendo ejecutable.
sudo chmod ug+x "$SCRIPT_PATH"

# ACL efectivas actuales.
sudo setfacl -R \
    -m "u:$USER:rwX,g::rwX,m::rwX,o::rX" \
    "$RUTA"

# ACL heredables para contenido futuro.
sudo find "$RUTA" -type d -exec setfacl \
    -m "d:u::rwx,d:u:$USER:rwx,d:g::rwx,d:m::rwx,d:o::r-x" \
    {} +

echo "Atlanticus: privileges assigned successfully"
