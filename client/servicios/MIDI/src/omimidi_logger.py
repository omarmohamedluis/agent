import logging
import sys
import os
from pathlib import Path

def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Obtiene un logger configurado para el servicio MIDI.
    Si es la primera vez que se llama para el logger raíz 'omimidi', se configura.
    """
    logger = logging.getLogger(name)
    
    # Si el logger ya tiene handlers, no lo re-configuramos
    # (Buscamos en la jerarquía hasta el logger 'omimidi')
    root_name = name.split('.')[0] if '.' in name else name
    root_logger = logging.getLogger(root_name)
    
    if not root_logger.handlers:
        _setup_logging(root_logger, level)
        
    return logger

def set_log_level(level: int):
    """Actualiza el nivel de log del logger raíz 'omimidi'."""
    root_logger = logging.getLogger("omimidi")
    root_logger.setLevel(level)
    for h in root_logger.handlers:
        h.setLevel(level)

def _setup_logging(logger: logging.Logger, level: int):
    logger.setLevel(level)
    
    # Evitar que los mensajes se propaguen al logger raíz de Python (que usa basicConfig)
    logger.propagate = False
    
    log_path = os.environ.get("OMI_LOG_PATH")
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
    
    handlers = []
    
    if log_path:
        # Modo gestionado por el cliente
        try:
            file_handler = logging.FileHandler(log_path, encoding='utf-8')
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except Exception as e:
            # Fallback a terminal si falla el archivo
            print(f"Error inicializando FileHandler en {log_path}: {e}", file=sys.stderr)
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(formatter)
            handlers.append(stream_handler)
    else:
        # Modo manual / local
        # Intentamos escribir en service.log relativo a la carpeta del servicio
        # (Asumiendo que estamos en src/, subimos un nivel)
        base_dir = Path(__file__).resolve().parents[1]
        local_log = base_dir / "service.log"
        
        try:
            file_handler = logging.FileHandler(local_log, encoding='utf-8')
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except Exception as e:
            print(f"Error inicializando FileHandler local en {local_log}: {e}", file=sys.stderr)
            
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        handlers.append(stream_handler)
        
    for h in handlers:
        logger.addHandler(h)
