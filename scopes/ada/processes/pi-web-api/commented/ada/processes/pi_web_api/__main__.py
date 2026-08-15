# Punto de entrada mínimo: delega todo el bootstrap al módulo estable del proceso.
# No compone dependencias ni lee configuración aquí para mantener una única ruta de arranque.
from ada.processes.pi_web_api.bootstrap import main

main()
