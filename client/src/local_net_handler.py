# configuracion de red local
# REVISAR 
# NO TERMINA DE FUNCIONAR AL 100% PERO ES MAS POR EL OS QUE POR EL SCRIPT, EVENTUALLMENTE TENDRÉ QUE PROBARLO BIEN Y BUSCAR WORKARROUNDS PARA QUE EL UX SEA MÁS FLUIDO

import ipaddress
import json
import subprocess
from pathlib import Path
from typing import Any

from logger import log_event, log_print

STRUCTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "structure.json"

module_name = f"{Path(__file__).parent.name}.{Path(__file__).stem}"

# helpers ------------------------------------------------------------------

def _read_structure() -> dict[str, Any]:
    if not STRUCTURE_PATH.exists():
        log_event("error", module_name, f"No se encontró {STRUCTURE_PATH}")
        raise FileNotFoundError(f"No se encontró {STRUCTURE_PATH}")
    with STRUCTURE_PATH.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _ensure_desired_block(structure: dict[str, Any]) -> dict[str, Any]:
    network_section = structure.setdefault("network", {})
    return network_section.setdefault("desired", {})


def _nmcli(args: list[str]) -> str:
    """Ejecuta un comando nmcli y devuelve su salida."""
    try:
        result = subprocess.run(
            ["nmcli", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as exc:  # noqa: BLE001
        log_event(
            "error",
            module_name,
            f"Fallo al ejecutar nmcli {' '.join(args)}: {exc.stderr.strip() if exc.stderr else exc}",
        )
        raise


def restart_network_manager() -> None:
    """Reinicia NetworkManager a través de systemctl."""
    try:
        subprocess.run(
            ["systemctl", "restart", "NetworkManager"],
            check=True,
            capture_output=True,
            text=True,
        )
        log_event("info", module_name, "NetworkManager reiniciado")
    except FileNotFoundError:
        log_event("warning", module_name, "systemctl no está disponible para reiniciar NetworkManager")
    except subprocess.CalledProcessError as exc:  # noqa: BLE001
        error_output = exc.stderr.strip() if exc.stderr else str(exc)
        log_event("error", module_name, f"Fallo al reiniciar NetworkManager: {error_output}")
    except Exception as exc:  # noqa: BLE001
        log_event("error", module_name, f"Error inesperado al reiniciar NetworkManager: {exc}")


def _list_connections() -> list[dict[str, str]]:
    """Devuelve una lista de conexiones con sus tipos y dispositivos."""
    try:
        output = _nmcli(["-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"])
    except subprocess.CalledProcessError:
        return []

    connections: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line:
            continue
        parts = line.split(":")
        name = parts[0]
        conn_type = parts[1] if len(parts) > 1 else ""
        device = parts[2] if len(parts) > 2 else ""
        connections.append({"name": name, "type": conn_type, "device": device})
    return connections


def _connection_matches_iface(connection: dict[str, str], iface: str) -> bool:
    """Comprueba si la conexión está asociada a la interfaz dada."""
    device = connection.get("device") or ""
    if device == iface:
        return True

    name = connection.get("name") or ""
    try:
        interface_name = _nmcli(
            ["-g", "connection.interface-name", "connection", "show", name]
        ).strip()
        if interface_name == iface:
            return True
    except subprocess.CalledProcessError:
        pass
    return False


def _find_connection(iface: str, allowed_types: set[str]) -> str | None:
    """
    Devuelve el nombre de la conexión que corresponde a la interfaz.

    - Si no se encuentra ninguna se registra un error y devuelve None.
    - Si hay más de una se registra un error y devuelve None.
    """
    connections = _list_connections()
    candidates = [
        conn
        for conn in connections
        if conn.get("type") in allowed_types and _connection_matches_iface(conn, iface)
    ]

    if not candidates:
        log_event(
            "error",
            module_name,
            f"No se encontró una conexión {allowed_types} para la interfaz {iface}",
        )
        return None

    if len(candidates) > 1:
        names = ", ".join(conn["name"] for conn in candidates)
        log_event(
            "error",
            module_name,
            f"Se encontraron múltiples conexiones ({names}) para la interfaz {iface}.",
        )
        return None

    return candidates[0]["name"]


def _get_connection_settings(connection_name: str) -> dict[str, str]:
    """Obtiene los parámetros IPv4 relevantes de la conexión."""
    keys = [
        "ipv4.method",
        "ipv4.addresses",
        "ipv4.gateway",
        "ipv4.dns",
        "ipv4.ignore-auto-dns",
        "ipv4.never-default",
    ]
    try:
        output = _nmcli(["-g", ",".join(keys), "connection", "show", connection_name])
    except subprocess.CalledProcessError:
        return {}

    values = output.splitlines()
    settings = dict(zip(keys, values))
    return settings


def _config_matches(connection_name: str, iface: str, config: dict[str, Any]) -> bool:
    """Comprueba si la configuración actual coincide con la deseada."""
    if not config:
        return True

    settings = _get_connection_settings(connection_name)
    if not settings:
        return False

    method = settings.get("ipv4.method", "")
    if config.get("dhcp"):
        if method != "auto":
            return False
        return True

    ip = config.get("ip")
    netmask = config.get("netmask")
    gateway = config.get("gateway")
    dns_primary = config.get("dns_primary") or "1.1.1.1"
    dns_secondary = config.get("dns_secondary") or "1.0.0.1"

    if not ip or not netmask or not gateway:
        log_event(
            "error",
            module_name,
            f"Configuración deseada incompleta para {iface}: falta ip/netmask/gateway",
        )
        return False

    prefix = _netmask_to_prefix(netmask)
    expected_address = f"{ip}/{prefix}"
    expected_gateway = gateway
    dns_values = [dns for dns in (dns_primary, dns_secondary) if dns]
    expected_dns = ",".join(dns_values)

    if method != "manual":
        return False

    current_address = settings.get("ipv4.addresses", "")
    current_gateway = settings.get("ipv4.gateway", "")
    current_dns = settings.get("ipv4.dns", "")
    current_ignore_auto = settings.get("ipv4.ignore-auto-dns", "")
    current_never_default = settings.get("ipv4.never-default", "")

    if current_address != expected_address:
        return False
    if current_gateway != expected_gateway:
        return False
    if current_dns != expected_dns:
        # nmcli podría repetir DNS con comas o dejar vacío
        current_dns_list = [value.strip() for value in current_dns.split(",") if value.strip()]
        expected_dns_list = [value.strip() for value in expected_dns.split(",") if value.strip()]
        if current_dns_list != expected_dns_list:
            return False

    if current_ignore_auto not in {"yes", "true", "1"}:
        return False
    if current_never_default not in {"yes", "true", "1"}:
        return False

    return True


def _netmask_to_prefix(netmask: str) -> int:
    """Convierte una máscara decimal a prefijo CIDR."""
    try:
        network = ipaddress.IPv4Network(f"0.0.0.0/{netmask}", strict=False)
        return network.prefixlen
    except ValueError as exc:  # noqa: BLE001
        raise ValueError(f"Máscara de red inválida: {netmask}") from exc


# funciones publicas -------------------------------------------------------

def _apply_interface_config(
    iface: str, connection_name: str, config: dict[str, Any]
) -> str:
    """
    Aplica la configuración deseada sobre la interfaz indicada.

    Utiliza NetworkManager (nmcli) para alternar entre DHCP y estático.
    """
    if not config:
        log_event("warning", module_name, f"Sin configuración deseada para {iface}; se omite")
        return "warning"

    if config.get("dhcp"):
        log_print("info", module_name, f"Aplicando DHCP en {iface} ({connection_name})")
        try:
            _nmcli(["connection", "mod", connection_name, "ipv4.method", "auto"])
            _nmcli(["connection", "mod", connection_name, "ipv4.addresses", ""])
            _nmcli(["connection", "mod", connection_name, "ipv4.gateway", ""])
            _nmcli(["connection", "mod", connection_name, "ipv4.dns", ""])
            _nmcli(["connection", "mod", connection_name, "ipv4.ignore-auto-dns", "no"])
            _nmcli(["connection", "mod", connection_name, "ipv4.routes", ""])
            _nmcli(["connection", "mod", connection_name, "ipv4.never-default", "no"])
            _nmcli(["connection", "up", connection_name, "ifname", iface])
        except subprocess.CalledProcessError:
            log_event("error", module_name, f"No se pudo aplicar DHCP en {iface}")
            return "error"
        return "ok"

    else:
        ip = config.get("ip")
        netmask = config.get("netmask")
        gateway = config.get("gateway")
        dns_primary = config.get("dns_primary") or "1.1.1.1"
        dns_secondary = config.get("dns_secondary") or "1.0.0.1"
        if not ip or not netmask or not gateway:
            log_event(
                "error",
                module_name,
                f"Configuración incompleta para {iface}: se requiere ip/netmask/gateway",
            )
            return "error"

        prefix = _netmask_to_prefix(netmask)
        address = f"{ip}/{prefix}"
        dns_values = [dns for dns in (dns_primary, dns_secondary) if dns]
        dns_string = ",".join(dns_values) if dns_values else ""

        log_print(
            "info",
            module_name,
            f"Configurando {iface} estática ({connection_name}): ip={address}, gateway={gateway}, dns={dns_string}",
        )

        try:
            # Cambia temporalmente a auto para poder limpiar direcciones previas
            _nmcli(["connection", "mod", connection_name, "ipv4.method", "auto"])

            # Limpia direcciones y rutas anteriores para evitar residuos
            _nmcli(["connection", "mod", connection_name, "ipv4.addresses", ""])
            _nmcli(["connection", "mod", connection_name, "ipv4.routes", ""])
            _nmcli(["connection", "mod", connection_name, "ipv4.gateway", ""])
            _nmcli(["connection", "mod", connection_name, "ipv4.dns", ""])
            _nmcli(["connection", "mod", connection_name, "ipv4.ignore-auto-dns", "no"])

            # Aplica la nueva dirección/gateway antes de fijar el modo manual
            _nmcli(["connection", "mod", connection_name, "ipv4.addresses", address])
            _nmcli(["connection", "mod", connection_name, "ipv4.gateway", gateway])

            if dns_string:
                _nmcli(["connection", "mod", connection_name, "ipv4.dns", dns_string])
                _nmcli(["connection", "mod", connection_name, "ipv4.ignore-auto-dns", "yes"])
            else:
                _nmcli(["connection", "mod", connection_name, "ipv4.dns", ""])
                _nmcli(["connection", "mod", connection_name, "ipv4.ignore-auto-dns", "no"])

            _nmcli(["connection", "mod", connection_name, "ipv4.never-default", "yes"])
            _nmcli(["connection", "mod", connection_name, "ipv4.method", "manual"])
            _nmcli(["connection", "up", connection_name, "ifname", iface])
        except subprocess.CalledProcessError:
            log_event("error", module_name, f"No se pudo aplicar configuración estática en {iface}")
            return "error"
        return "ok"


def net_default() -> dict[str, Any]:
    """Configura la red principal de la Pi según lo declarado en network.desired."""
    log_print("info", module_name, "Iniciando configuración de red deseada")
    structure = _read_structure()
    desired = _ensure_desired_block(structure)

    ethernet_cfg = desired.get("eth0") or {}
    wifi_cfg = desired.get("wlan0") or {}

    result: dict[str, Any] = {"ethernet": ethernet_cfg, "wifi": wifi_cfg}

    def process_interface(
        iface: str, config: dict[str, Any], allowed_types: set[str], label: str
    ) -> str:
        if not config:
            log_event("warning", module_name, f"No hay configuración deseada para {iface}")
            return "warning"

        connection_name = _find_connection(iface, allowed_types)
        if not connection_name:
            return "error"

        if _config_matches(connection_name, iface, config):
            log_event(
                "info",
                module_name,
                f"La conexión {connection_name} ya coincide con la configuración deseada ({label}).",
            )
            return "ok"

        if config.get("dhcp"):
            return _apply_interface_config(iface, connection_name, config)

        # Asegura DNS predeterminados cuando se trabaja en manual
        config.setdefault("dns_primary", "1.1.1.1")
        config.setdefault("dns_secondary", "1.0.0.1")

        return _apply_interface_config(iface, connection_name, config)

    statuses = []
    statuses.append(process_interface("eth0", ethernet_cfg, {"802-3-ethernet", "ethernet"}, "ethernet"))
    statuses.append(process_interface("wlan0", wifi_cfg, {"802-11-wireless", "wifi"}, "wireless"))
    statuses = [status for status in statuses if status]

    # restart_network_manager()

    log_event("info", module_name, f"Configuración principal aplicada: {result}")
    if "error" in statuses:
        log_print("error", module_name, "Error al configurar la red. Revisa components.log")
    elif "warning" in statuses:
        log_print("warning", module_name, "Red configurada con advertencias. Revisa components.log")
    else:
        log_print("info", module_name, "Red configurada correctamente.")

    return result
