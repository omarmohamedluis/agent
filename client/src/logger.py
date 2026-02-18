"""
Configuración de Logging Estandarizada.
Configura el sistema de logs para rotar archivos, limpiar logs antiguos
y mantener un formato consistente.
"""
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from os_utils import fix_permissions

# Configuración de Rutas
BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BASE_DIR / "logs" / "components"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "components.log"

# Formateador personalizado para coincidir con estilo previo
class CustomFormatter(logging.Formatter):
    def format(self, record):
        record.caller = record.name  # Usar nombre del logger como caller
        return super().format(record)

FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S,%f"

def configure_logging():
    """Configura el logger raíz para escribir en un nuevo archivo con timestamp por inicio."""
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Generar nombre de archivo con timestamp
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"components_{timestamp}.log"
    current_log_file = LOG_DIR / log_filename
    
    # File Handler (Nuevo archivo por ejecución)
    file_handler = logging.FileHandler(current_log_file, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATE_FORMAT[:-3]))
    root_logger.addHandler(file_handler)
    
    # Reparar permisos del nuevo log
    fix_permissions(current_log_file)
    
    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(FORMAT, datefmt=DATE_FORMAT[:-3]))
    root_logger.addHandler(console_handler)

    # Limpieza de logs antiguos (Mantener últimos 20)
    try:
        log_files = sorted(LOG_DIR.glob("components_*.log"), key=lambda p: p.stat().st_mtime)
        while len(log_files) > 20:
            oldest = log_files.pop(0)
            try:
                oldest.unlink()
            except Exception as e:
                print(f"Fallo al eliminar log antiguo {oldest}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Error limpiando logs antiguos: {e}", file=sys.stderr)

    # Separador de Inicio
    if not getattr(configure_logging, "has_run", False):
        separator = f"\n{'='*30} INICIADO EN \"{timestamp}\" {'='*30}\n"
        root_logger.info(separator)
        configure_logging.has_run = True

def get_logger(name: str) -> logging.Logger:
    """Retorna un logger configurado"""
    return logging.getLogger(name)

# Wrappers para compatibilidad hacia atrás
def log_event(level: str, caller: str, message: str) -> Path:
    logger = logging.getLogger(caller)
    lvl = getattr(logging, (level or "INFO").upper(), logging.INFO)
    logger.log(lvl, message)
    return LOG_FILE

def log_print(level: str, caller: str, message: str) -> Path:
    log_event(level, caller, message)
    # Print es manejado por StreamHandler si se añade, o podemos forzar print aquí si es necesario
    # Por ahora, confiar en los handlers de logging es más limpio.