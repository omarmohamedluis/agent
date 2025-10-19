# aqui se haran centraran las comunicaciones del sistema

import random
import time


def handshake() -> bool:
    """Simula el handshake con el servidor. De momento espera y retorna False."""
    time.sleep(5)
    return False


def check_server_status() -> bool:
    """Devuelve True o False de forma aleatoria (~50% de probabilidad)."""
    return random.random() >= 0.5
