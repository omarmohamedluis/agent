import os
from pathlib import Path

def fix_permissions(path: Path):
    """
    Cambia la propiedad del archivo o directorio al usuario que invocó sudo.
    Si no se detecta sudo (SUDO_UID/SUDO_GID), no hace nada.
    """
    sudo_uid = os.environ.get('SUDO_UID')
    sudo_gid = os.environ.get('SUDO_GID')
    
    if sudo_uid and sudo_gid:
        try:
            uid = int(sudo_uid)
            gid = int(sudo_gid)
            
            if path.exists():
                # Cambiar el path principal
                os.chown(path, uid, gid)
                
                # Si es un directorio, recursivo (pero con cuidado)
                if path.is_dir():
                    for root, dirs, files in os.walk(path):
                        for item in dirs + files:
                            os.chown(os.path.join(root, item), uid, gid)
        except Exception as e:
            # Fallo silencioso para no romper la ejecución principal por temas de permisos
            pass
