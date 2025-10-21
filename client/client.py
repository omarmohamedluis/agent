# punto de entrada, desde aqui iniciare las cosas,

import subprocess
import sys
from pathlib import Path
import time

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.append(str(BASE_DIR))
sys.path.append(str(SRC_DIR))

from logger import log_event, log_print         # type: ignore
from JsonConfig import InitJson, UpdateNet      # type: ignore
from local_net_handler import net_default       # type: ignore
from ui import LoadingUI, StartStandardUI, StopStandardUI,UIOFF  # type: ignore
from heartbeat import start_heartbeat, stop_heartbeat, get_heartbeat_snapshot  # type: ignore
from NetComHandler import handshake            # type: ignore


module_name = f"{Path(__file__).parent.name}.{Path(__file__).stem}"

#    helpers

def _turn_off_sytem() -> None:
    log_print("info", module_name, "Apagando interfaces locales")
    StopStandardUI()
    stop_heartbeat()
    log_print("info", module_name, "Interfaz y heartbeat detenidos")
    UIOFF()


# funciones

def inicializar():
    LoadingUI(0,"INICIANDO")


 
    log_print("info", module_name, "Iniciando cliente")
    LoadingUI(10,"JSON")
    InitJson()
    log_print("info", module_name, "json cargando, iniciamos red")
    LoadingUI(20,"NETCONF")
    net_default()
    log_print("info", module_name, "net finalizado, actualizando structure.json")
    LoadingUI(30,"JSON2")
    UpdateNet()
    log_print("info", module_name, "sistema iniciado, starting heartbeat")
    LoadingUI(40,"heartbeat")
    start_heartbeat()
    LoadingUI(70,"HANDSHAKE")
    if handshake():
        log_print("info", module_name, "El servidor ha respondido al handshake, iniciando servicio")
    else:
        log_print("warning", module_name, "No se encontró el servidor en el tiempo de espera, iniciando servicio")

    LoadingUI(80,"SERVICE START")

    time.sleep(5)

    LoadingUI(100,"done")

def main() -> None:
    log_print("info", module_name, "Activando LCD UI estándar")
    try:
        StartStandardUI()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log_print("warning", module_name, "Interrupción detectada, apagando cliente")
    finally:
        _turn_off_sytem()
        log_print("info", module_name, "Cliente detenido correctamente")


def shutdown() -> None:
    log_print("info", module_name, "Solicitando apagado del sistema")
    _turn_off_sytem()
    subprocess.run(["sudo", "shutdown", "now"], check=True)


def reboot() -> None:
    log_print("info", module_name, "Solicitando reinicio del sistema")
    _turn_off_sytem()
    subprocess.run(["sudo", "reboot", "now"], check=True)



if __name__ == "__main__":
    inicializar()
    main()
