# punto de entrada, desde aqui iniciare las cosas,

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
sys.path.append(str(BASE_DIR))
sys.path.append(str(SRC_DIR))

from logger import log_event, log_print  # type: ignore


def inicializar():
    module_name = f"{Path(__file__).parent.name}.{Path(__file__).stem}"
    log_print("info", module_name, "Iniciando cliente")


    

def main() -> None:
    log_print("info", __name__ , "Iniciando cliente")









if __name__ == "__main__":
    inicializar()

