from typing import Dict, Any
from heartbeat import get_heartbeat_snapshot, start_heartbeat

# Ensure heartbeat is running
# start_heartbeat() # Removed to avoid side effects on import

def get_system_status() -> Dict[str, Any]:
    snapshot = get_heartbeat_snapshot()
    
    # Extract primary IP
    ip = "Unknown"
    ifaces = snapshot.get("ifaces", [])
    for iface in ifaces:
        if iface.get("ip"):
            ip = iface.get("ip")
            break
            
    return {
        "cpu": snapshot.get("cpu"),
        "temp": snapshot.get("temp"),
        "ip": ip,
        "ifaces": ifaces
    }
