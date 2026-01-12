import os
import json
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict
from datetime import datetime, timezone

LOGGER = logging.getLogger("omimidi.utils")

def load_json(path: str | Path, default: Any) -> Any:
    """Carga un archivo JSON de forma segura."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str | Path, data: Any, backup: bool = True) -> None:
    """
    Guarda datos JSON de forma segura usando un archivo temporal.
    Si data es un diccionario, añade 'updated_at' y asegura el orden de las claves.
    """
    path_obj = Path(path)
    directory = path_obj.parent
    if directory:
        directory.mkdir(parents=True, exist_ok=True)

    # ... (rest of metadata logic) ...
    if isinstance(data, dict):
        # Asegurar que file_info existe
        if "file_info" not in data:
            data["file_info"] = {}
        
        # Actualizar timestamp dentro de file_info
        data["file_info"]["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        # Definir orden de claves prioritarias
        priority_keys = ["file_info", "source", "net", "osc", "routes"]
        ordered_data = {}
        
        # 1. Claves prioritarias
        for key in priority_keys:
            if key in data:
                ordered_data[key] = data[key]
        
        # 2. El resto de claves
        for key in sorted(data.keys()):
            if key not in priority_keys:
                ordered_data[key] = data[key]
        
        data = ordered_data

    backup_path = None
    # Crear backup si existe y se solicita
    if backup and path_obj.exists():
        backup_path = path_obj.with_suffix(path_obj.suffix + ".bak")
        try:
            shutil.copy2(path_obj, backup_path)
        except FileNotFoundError:
            # Race condition: el archivo desapareció justo antes de copiar
            backup_path = None
        except Exception as e:
            LOGGER.warning("No se pudo crear backup de %s: %s", path, e)
            backup_path = None

    # Guardar nuevo contenido usando archivo temporal
    fd, tmp_path = tempfile.mkstemp(
        dir=directory if directory.exists() else None, 
        prefix=path_obj.name + '.', 
        suffix='.tmp'
    )
    
    success = False
    try:
        with os.fdopen(fd, 'w', encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # Reemplazo atómico
        os.replace(tmp_path, path)
        
        # Asegurar permisos 664 (rw-rw-r--)
        try:
            os.chmod(path, 0o664)
        except Exception:
            pass
            
        success = True
    except Exception as e:
        LOGGER.error("Error guardando %s: %s", path, e)
        # Intentar restaurar backup si falló el reemplazo
        if backup_path and backup_path.exists():
            try:
                os.replace(backup_path, path_obj)
                LOGGER.info("Backup restaurado para %s", path)
            except Exception as e2:
                LOGGER.error("No se pudo restaurar backup de %s: %s", path, e2)
        raise
    finally:
        # Limpiar archivos temporales
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            
        # Eliminar backup si todo fue bien
        if backup_path and success and backup_path.exists():
            try:
                os.unlink(backup_path)
            except Exception:
                pass
