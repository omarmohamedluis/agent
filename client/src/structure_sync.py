import sys
from structure_manager import get_structure_manager
from logger import get_logger, configure_logging

LOGGER = get_logger("omiclient.structure_sync")

def sync_service(svc_id: str) -> bool:
    """
    Wrapper for StructureManager.sync_service_metadata to maintain backward compatibility
    with external scripts calling this file.
    """
    manager = get_structure_manager()
    try:
        manager.sync_service_metadata(svc_id)
        return True
    except Exception as e:
        LOGGER.error(f"Failed to sync service {svc_id}: {e}")
        return False

if __name__ == "__main__":
    configure_logging()
    if len(sys.argv) > 1:
        svc_id = sys.argv[1]
        if sync_service(svc_id):
            print(f"Successfully synced {svc_id}")
        else:
            print(f"No changes or error syncing {svc_id}")
            sys.exit(1)
    else:
        print("Usage: python3 structure_sync.py <service_id>")
        sys.exit(1)
