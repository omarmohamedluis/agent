#nuevo log generator

import os
from datetime import datetime
from pathlib import Path
import re

# Cambia este valor si quieres escribir en otro archivo.
Log_name = "server"

#ruta para llegar ha donde se guarda el log
BASE_DIR = Path(__file__).resolve().parents[1]  # llega a agent/
LOG_DIR = BASE_DIR / "logs" / "server"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_ROOT = LOG_DIR / f"{Log_name}.log"


LEVEL_COLORS = {
    "ERROR": "\033[31m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
}
DEFAULT_COLOR = "\033[34m"
RESET_COLOR = "\033[0m"

TIMESTAMP_COLOR = "\033[92m"  # verde claro
CALLER_COLOR = "\033[36m"     # celeste
ERROR_WORD_COLOR = "\033[31m"


#   helpers

def _highlight_error_words(text: str) -> str:
    def _replace(match):
        return f"{ERROR_WORD_COLOR}{match.group(0)}{RESET_COLOR}"
    return re.sub(r"error", _replace, text, flags=re.IGNORECASE)

# llamadas desde fuera

def log_event(level: str, caller: str, message: str) -> Path:

    level = (level or "").upper() or "INFO"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = LOG_ROOT / f"{Log_name}.log"

    line = f"{timestamp} [{level}] {caller}: {message}"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + os.linesep)

    return log_path

def log_print(level: str, caller: str, message: str) -> Path:
    level_norm = (level or "").upper() or "INFO"

    log_event(level_norm, caller, message)

    level_color = LEVEL_COLORS.get(level_norm, DEFAULT_COLOR)
    tag_colored = f"{level_color}[{level_norm}]{RESET_COLOR}"
    caller_colored = f"{CALLER_COLOR}{caller}{RESET_COLOR}"
    message_colored = _highlight_error_words(message)

    print(f"{tag_colored} {caller_colored}: {message_colored}")