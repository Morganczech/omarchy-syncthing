#!/usr/bin/env python3
"""Hardened local Syncthing controller and status probe for Omarchy."""

import errno
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlencode
import xml.etree.ElementTree as ET

MAX_XML_BYTES = 2 * 1024 * 1024
MAX_API_BYTES = 2 * 1024 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_KBS = 10 * 1024 * 1024  # 10 GiB/s ceiling in KiB/s
MAX_FOLDERS = 100
MAX_DEVICES = 64
LINK_LOCAL_PROFILE = "omarchy-syncthing-lan"

API_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
DEVICE_ID_RE = re.compile(r"^[A-Z2-7]{7}(?:-[A-Z2-7]{7}){7}$|^[A-Z0-9]{7}(?:-[A-Z0-9]{7}){7}$")
FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
DOCTYPES_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

VIRTUAL_IFACE_PREFIXES = (
    "lo",
    "veth",
    "docker",
    "br-",
    "virbr",
    "tailscale",
    "wg",
    "tun",
    "tap",
    "zt",
    "vmnet",
    "vboxnet",
    "podman",
    "cni",
)

TRANSPORT_MODES = {"auto", "ethernet", "wifi"}
BANDWIDTH_PRESETS = {
    "unlimited": (0, 0),
    "25m": (25 * 1024, 25 * 1024),
    "10m": (10 * 1024, 10 * 1024),
    "2m": (2 * 1024, 2 * 1024),
}


class ControllerError(Exception):
    """Controlled validation or operational error safe to report to UI."""


def sanitize_label(value, max_len=80):
    """Strip control characters and collapse whitespace for safe plain-text UI."""
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in value)
    return " ".join(cleaned.split())[:max_len]


def is_valid_device_id(value):
    return isinstance(value, str) and bool(DEVICE_ID_RE.fullmatch(value))


def is_valid_folder_id(value):
    return (
        isinstance(value, str)
        and bool(FOLDER_ID_RE.fullmatch(value))
        and value not in {".", ".."}
        and ".." not in value
    )


def is_valid_iface_name(value):
    return (
        isinstance(value, str)
        and bool(IFACE_RE.fullmatch(value))
        and value not in {".", ".."}
    )


def is_virtual_iface(name):
    if name == "lo":
        return True
    return any(name.startswith(prefix) for prefix in VIRTUAL_IFACE_PREFIXES if prefix != "lo")


def default_state():
    return {
        "transportMode": "auto",
        "autoPaused": False,
        "autoPausedDevices": [],
        "baselineListenAddresses": None,
        "baselineAllowedNetworks": {},
        "baselineDeviceAddresses": {},
        "bandwidthPreset": "unlimited",
        "configuredRecvKbps": 0,
        "configuredSendKbps": 0,
        "limitWifiOnly": False,
        "lastEthernetUp": False,
        "upgradedPeers": [],
    }


def normalize_state(raw):
    state = default_state()
    if not isinstance(raw, dict):
        return state

    mode = raw.get("transportMode")
    if mode in TRANSPORT_MODES:
        state["transportMode"] = mode

    state["autoPaused"] = bool(raw.get("autoPaused", False))

    paused_devs = raw.get("autoPausedDevices")
    if isinstance(paused_devs, list):
        state["autoPausedDevices"] = [d for d in paused_devs[:MAX_DEVICES] if is_valid_device_id(d)]

    listen = raw.get("baselineListenAddresses")
    if isinstance(listen, list) and all(isinstance(x, str) and len(x) <= 256 for x in listen[:32]):
        state["baselineListenAddresses"] = list(listen[:32])

    allowed = raw.get("baselineAllowedNetworks")
    if isinstance(allowed, dict):
        clean_allowed = {}
        for dev_id, nets in list(allowed.items())[:MAX_DEVICES]:
            if is_valid_device_id(dev_id) and isinstance(nets, list):
                clean_nets = [n for n in nets[:32] if isinstance(n, str) and len(n) <= 128]
                clean_allowed[dev_id] = clean_nets
        state["baselineAllowedNetworks"] = clean_allowed

    dev_addrs = raw.get("baselineDeviceAddresses")
    if isinstance(dev_addrs, dict):
        clean_addrs = {}
        for dev_id, addrs in list(dev_addrs.items())[:MAX_DEVICES]:
            if is_valid_device_id(dev_id) and isinstance(addrs, list):
                clean_list = [a for a in addrs[:32] if isinstance(a, str) and len(a) <= 256]
                clean_addrs[dev_id] = clean_list
        state["baselineDeviceAddresses"] = clean_addrs

    preset = raw.get("bandwidthPreset")
    if preset in BANDWIDTH_PRESETS or preset == "custom":
        state["bandwidthPreset"] = preset

    for key in ("configuredRecvKbps", "configuredSendKbps"):
        val = raw.get(key)
        if isinstance(val, int) and not isinstance(val, bool) and 0 <= val <= MAX_KBS:
            state[key] = val

    state["limitWifiOnly"] = bool(raw.get("limitWifiOnly", False))
    state["lastEthernetUp"] = bool(raw.get("lastEthernetUp", False))

    upgraded = raw.get("upgradedPeers")
    if isinstance(upgraded, list):
        state["upgradedPeers"] = [d for d in upgraded[:MAX_DEVICES] if is_valid_device_id(d)]

    return state


class StateStore:
    """Symlink-safe, 0600-permission atomic state storage."""

    def __init__(self, base_dir=None):
        if base_dir is not None:
            self.directory = Path(base_dir)
        else:
            home = Path.home()
            xdg_state = Path(os.environ.get("XDG_STATE_HOME") or (home / ".local/state"))
            self.directory = xdg_state / "omarchy-syncthing"
        self.path = self.directory / "state.json"

    def _ensure_safe_dir(self):
        if self.directory.is_symlink():
            raise ControllerError("State directory must not be a symlink")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        st = os.lstat(self.directory)
        if not stat.S_ISDIR(st.st_mode):
            raise ControllerError("State path is not a directory")
        if st.st_uid != os.getuid():
            raise ControllerError("State directory owner mismatch")
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass

    def load(self):
        if not self.directory.exists():
            return default_state()
        if self.directory.is_symlink() or self.path.is_symlink():
            raise ControllerError("Refusing to read symlinked state file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(self.path, flags)
        except FileNotFoundError:
            return default_state()
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ControllerError("Refusing to read symlinked state file") from exc
            return default_state()
        try:
            with os.fdopen(fd, "rb") as handle:
                data = handle.read(MAX_STATE_BYTES + 1)
            if len(data) > MAX_STATE_BYTES:
                return default_state()
            return normalize_state(json.loads(data.decode("utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return default_state()

    def save(self, state):
        self._ensure_safe_dir()
        if self.path.is_symlink():
            raise ControllerError("Refusing to overwrite symlinked state file")
        clean = normalize_state(state)
        payload = json.dumps(clean, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        fd, tmp_name = tempfile.mkstemp(dir=str(self.directory), prefix=".state.")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if self.path.is_symlink():
                raise ControllerError("Refusing to overwrite symlinked state file")
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise


def parse_loopback_gui(xml_bytes):
    """Extract (host, port, use_tls, apikey) from config.xml safely."""
    if len(xml_bytes) > MAX_XML_BYTES:
        raise ValueError("oversized configuration")
    if DOCTYPES_RE.search(xml_bytes):
        raise ValueError("disallowed XML declaration")
    root = ET.fromstring(xml_bytes)
    gui = root.find("gui")
    if gui is None:
        raise FileNotFoundError("missing gui section")
    if gui.attrib.get("enabled", "true").lower() == "false":
        raise FileNotFoundError("Syncthing GUI disabled")

    address = (gui.findtext("address") or "").strip()
    host = ""
    port_str = ""
    if address.startswith("["):
        bracket_end = address.find("]")
        if bracket_end > 1 and address[bracket_end + 1 : bracket_end + 2] == ":":
            host = address[1:bracket_end]
            port_str = address[bracket_end + 2 :]
    elif ":" in address:
        host, port_str = address.rsplit(":", 1)

    if host == "localhost":
        host = "127.0.0.1"
    if host not in {"127.0.0.1", "::1"}:
        raise ValueError("Syncthing GUI is not bound to loopback")

    port = int(port_str)
    if not (1 <= port <= 65535):
        raise ValueError("invalid GUI port")

    key = (gui.findtext("apikey") or "").strip()
    if not API_KEY_RE.fullmatch(key):
        raise ValueError("invalid or missing API key")

    use_tls = gui.attrib.get("tls", "false").lower() == "true"
    return host, port, use_tls, key


def discover_syncthing_endpoint(search_dirs=None):
    if search_dirs is None:
        home = Path.home()
        state = Path(os.environ.get("XDG_STATE_HOME") or (home / ".local/state"))
        config = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
        search_dirs = (state / "syncthing", config / "syncthing", home / ".syncthing")

    for directory in search_dirs:
        cfg_path = Path(directory) / "config.xml"
        if cfg_path.is_symlink():
            continue
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(cfg_path, flags)
        except FileNotFoundError:
            continue
        except OSError:
            continue
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(MAX_XML_BYTES + 1)
        return parse_loopback_gui(data)

    raise FileNotFoundError("local Syncthing configuration unavailable")


class SyncthingClient:
    """Loopback-only HTTP/HTTPS REST client that keeps the API key internal."""

    def __init__(self, host, port, use_tls, key, timeout=2.0):
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self._key = key
        self.timeout = timeout
        self._conn = None

    @property
    def gui_url(self):
        scheme = "https" if self.use_tls else "http"
        rendered_host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{scheme}://{rendered_host}:{self.port}/"

    def _connect(self):
        if self._conn is not None:
            return self._conn
        if self.use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._conn = http.client.HTTPSConnection(
                self.host, self.port, timeout=self.timeout, context=ctx
            )
        else:
            self._conn = http.client.HTTPConnection(
                self.host, self.port, timeout=self.timeout
            )
        return self._conn

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def request(self, method, path, payload=None):
        body = None
        headers = {"X-API-Key": self._key, "Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        for attempt in range(2):
            conn = self._connect()
            try:
                conn.request(method, "/rest/" + path.lstrip("/"), body=body, headers=headers)
                response = conn.getresponse()
                raw = response.read(MAX_API_BYTES + 1)
                if len(raw) > MAX_API_BYTES:
                    raise ValueError("oversized API response")
                if response.status not in (200, 204):
                    raise ValueError(f"unexpected API status {response.status}")
                if not raw or not raw.strip():
                    return {}
                return json.loads(raw.decode("utf-8"))
            except (http.client.NotConnected, http.client.BadStatusLine, BrokenPipeError):
                self.close()
                if attempt == 1:
                    raise

        return {}

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, payload=None):
        return self.request("POST", path, payload=payload)

    def patch(self, path, payload):
        return self.request("PATCH", path, payload=payload)


def classify_sysfs_iface(name, sys_net=Path("/sys/class/net")):
    if not is_valid_iface_name(name) or is_virtual_iface(name):
        return "unknown", False
    dev_dir = sys_net / name
    if not dev_dir.exists():
        return "unknown", False

    carrier = False
    try:
        carrier = (dev_dir / "carrier").read_text(encoding="utf-8").strip() == "1"
    except OSError:
        try:
            carrier = (dev_dir / "operstate").read_text(encoding="utf-8").strip() == "up"
        except OSError:
            carrier = False

    if (dev_dir / "wireless").exists() or (dev_dir / "phy80211").exists():
        return "wifi", carrier

    try:
        if (dev_dir / "device").exists() and (dev_dir / "type").read_text(encoding="utf-8").strip() == "1":
            return "ethernet", carrier
    except OSError:
        pass

    return "unknown", carrier


def active_nm_link_local_iface():
    nmcli = Path("/usr/bin/nmcli")
    if not nmcli.is_file():
        return ""
    try:
        proc = subprocess.run(
            ["/usr/bin/nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            capture_output=True,
            text=True,
            check=False,
            timeout=0.8,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    for line in proc.stdout.splitlines():
        parts = line.split(":", 1)
        if len(parts) == 2 and parts[0] == LINK_LOCAL_PROFILE and is_valid_iface_name(parts[1]):
            return parts[1]
    return ""


def probe_interfaces(sys_net=Path("/sys/class/net"), ip_bin="/usr/bin/ip"):
    """Return structured physical Ethernet and Wi-Fi link & IPv4/IPv6 subnet state."""
    addr_map = {}
    try:
        proc = subprocess.run(
            [ip_bin, "-j", "addr"],
            capture_output=True,
            text=True,
            check=True,
            timeout=0.8,
        )
        for entry in json.loads(proc.stdout):
            ifname = entry.get("ifname", "")
            if is_valid_iface_name(ifname):
                addr_map[ifname] = entry.get("addr_info", [])
    except (OSError, ValueError, subprocess.SubprocessError):
        addr_map = {}

    ll_iface = active_nm_link_local_iface()
    ethernet_ifaces = []
    wifi_ifaces = []
    cable_without_ip = None

    try:
        names = sorted(p.name for p in sys_net.iterdir())
    except OSError:
        names = []

    for name in names:
        kind, carrier = classify_sysfs_iface(name, sys_net=sys_net)
        if kind not in {"ethernet", "wifi"}:
            continue

        ipv4_list = []
        ipv6_list = []
        cidrs = []
        for info in addr_map.get(name, []):
            family = info.get("family")
            local = info.get("local")
            prefixlen = info.get("prefixlen")
            if not isinstance(local, str) or not isinstance(prefixlen, int):
                continue
            try:
                iface_ip = ipaddress.ip_interface(f"{local.split('%')[0]}/{prefixlen}")
            except ValueError:
                continue
            if family == "inet" and not iface_ip.ip.is_loopback:
                ip_str = str(iface_ip.ip)
                cidr_str = str(iface_ip.network)
                ipv4_list.append(ip_str)
                if cidr_str not in cidrs:
                    cidrs.append(cidr_str)
            elif family == "inet6" and not iface_ip.ip.is_loopback and not iface_ip.ip.is_link_local:
                ip_str = str(iface_ip.ip)
                cidr_str = str(iface_ip.network)
                ipv6_list.append(ip_str)
                if cidr_str not in cidrs:
                    cidrs.append(cidr_str)

        record = {
            "name": name,
            "carrier": carrier,
            "ipv4": ipv4_list,
            "ipv6": ipv6_list,
            "cidrs": cidrs,
            "ip": ipv4_list[0] if ipv4_list else (ipv6_list[0] if ipv6_list else ""),
            "cidr": cidrs[0] if cidrs else "",
            "linkLocalProfile": name == ll_iface,
        }
        if kind == "ethernet":
            ethernet_ifaces.append(record)
            if carrier and not ipv4_list and not ipv6_list and cable_without_ip is None:
                cable_without_ip = {
                    "interface": name,
                    "nmAvailable": Path("/usr/bin/nmcli").is_file(),
                }
        else:
            wifi_ifaces.append(record)

    eth_ready = [i for i in ethernet_ifaces if i["carrier"] and i["cidrs"]]
    wifi_ready = [i for i in wifi_ifaces if i["carrier"] and i["cidrs"]]

    return {
        "ethernet": {
            "available": bool(eth_ready),
            "carrier": any(i["carrier"] for i in ethernet_ifaces),
            "interfaces": ethernet_ifaces,
            "readyInterfaces": eth_ready,
        },
        "wifi": {
            "available": bool(wifi_ready),
            "carrier": any(i["carrier"] for i in wifi_ifaces),
            "interfaces": wifi_ifaces,
            "readyInterfaces": wifi_ready,
        },
        "cableWithoutIp": cable_without_ip,
        "directLinkLocalInterface": ll_iface,
    }


def parse_peer_ip(address_field):
    if not isinstance(address_field, str) or not address_field:
        return None
    raw = address_field.strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0]
    host = raw.rsplit(":", 1)[0].strip("[]")
    host = host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def connection_network_type(connection, sys_net=Path("/sys/class/net"), ip_bin="/usr/bin/ip"):
    try:
        peer_ip = parse_peer_ip(connection.get("address", ""))
        if peer_ip is None:
            return "unknown", ""
        route = subprocess.run(
            [ip_bin, "-j", "route", "get", str(peer_ip)],
            capture_output=True,
            text=True,
            check=True,
            timeout=0.3,
        )
        iface = json.loads(route.stdout)[0].get("dev", "")
        kind, _ = classify_sysfs_iface(iface, sys_net=sys_net)
        return kind, str(peer_ip)
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
        peer_ip = parse_peer_ip(connection.get("address", ""))
        return "unknown", str(peer_ip) if peer_ip else ""


def build_listen_addresses(ready_ifaces):
    addrs = []
    for iface in ready_ifaces:
        for ip4 in iface["ipv4"][:4]:
            addrs.append(f"tcp4://{ip4}:22000")
            addrs.append(f"quic4://{ip4}:22000")
        for ip6 in iface["ipv6"][:4]:
            addrs.append(f"tcp6://[{ip6}]:22000")
            addrs.append(f"quic6://[{ip6}]:22000")
    return addrs


def build_allowed_cidrs(ready_ifaces):
    cidrs = []
    for iface in ready_ifaces:
        for cidr in iface["cidrs"]:
            if cidr not in cidrs:
                cidrs.append(cidr)
    return cidrs


def parse_peer_endpoint(address_field, default_port=22000):
    if not isinstance(address_field, str) or not address_field:
        return None, default_port
    raw = address_field.strip()
    if raw == "dynamic":
        return None, default_port
    if "://" in raw:
        scheme, rest = raw.split("://", 1)
        if not scheme.startswith(("tcp", "quic")):
            return None, default_port
        raw = rest
    raw = raw.split("/", 1)[0].split("?", 1)[0]
    host = raw
    port = default_port
    if raw.startswith("["):
        end = raw.find("]")
        if end > 1:
            host = raw[1:end]
            if raw[end + 1 : end + 2] == ":":
                try:
                    port = int(raw[end + 2 :], 10)
                except ValueError:
                    port = default_port
    elif ":" in raw:
        h_part, p_part = raw.rsplit(":", 1)
        host = h_part
        try:
            port = int(p_part, 10)
        except ValueError:
            port = default_port
    host = host.split("%", 1)[0]
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        return None, default_port
    if not (1 <= port <= 65535):
        port = default_port
    return ip_obj, port


def peer_endpoints_in_cidrs(orig_addresses, discovery_cache, peer_id, cidrs):
    """Return [(ip_str, port)] for peer endpoints belonging to cidrs."""
    if not cidrs:
        return []
    networks = []
    for c in cidrs:
        try:
            networks.append(ipaddress.ip_network(c, strict=False))
        except ValueError:
            continue
    if not networks:
        return []

    candidates = []
    for addr in (orig_addresses or []):
        ip_obj, port = parse_peer_endpoint(addr)
        if ip_obj and any(ip_obj in net for net in networks):
            pair = (str(ip_obj), port)
            if pair not in candidates:
                candidates.append(pair)

    if isinstance(discovery_cache, dict):
        entry = discovery_cache.get(peer_id)
        disco_addrs = (
            entry.get("addresses", [])
            if isinstance(entry, dict)
            else (entry if isinstance(entry, list) else [])
        )
        for addr in disco_addrs:
            ip_obj, port = parse_peer_endpoint(addr)
            if ip_obj and any(ip_obj in net for net in networks):
                pair = (str(ip_obj), port)
                if pair not in candidates:
                    candidates.append(pair)

    return candidates


def can_reach_peer_tcp(ip_str, port, timeout=0.35):
    try:
        with socket.create_connection((ip_str, port), timeout=timeout):
            return True
    except OSError:
        return False


def save_device_baseline_if_missing(state, dev):
    dev_id = dev["deviceID"]
    allowed_map = state.setdefault("baselineAllowedNetworks", {})
    addrs_map = state.setdefault("baselineDeviceAddresses", {})
    changed = False
    if dev_id not in allowed_map:
        allowed_map[dev_id] = list(dev.get("allowedNetworks") or [])
        changed = True
    if dev_id not in addrs_map:
        addrs_map[dev_id] = list(dev.get("addresses") or ["dynamic"])
        changed = True
    return changed


def restore_single_device_baseline(client, state, dev):
    dev_id = dev["deviceID"]
    allowed_map = state.get("baselineAllowedNetworks") or {}
    addrs_map = state.get("baselineDeviceAddresses") or {}
    if dev_id not in allowed_map and dev_id not in addrs_map:
        return False

    dev_patch = {}
    if dev_id in allowed_map:
        orig_nets = allowed_map.pop(dev_id)
        if (dev.get("allowedNetworks") or []) != orig_nets:
            dev_patch["allowedNetworks"] = orig_nets
    if dev_id in addrs_map:
        orig_addrs = addrs_map.pop(dev_id)
        if (dev.get("addresses") or []) != orig_addrs:
            dev_patch["addresses"] = orig_addrs
    if dev_patch:
        client.patch(f"config/devices/{quote(dev_id, safe='')}", dev_patch)
        dev.update(dev_patch)
    return True


def filter_device_addresses_for_cidrs(orig_addresses, cidrs):
    """Filter static IP peer addresses to matching CIDRs while preserving dynamic discovery."""
    if not isinstance(orig_addresses, list) or not orig_addresses:
        return ["dynamic"]
    networks = []
    for c in cidrs:
        try:
            networks.append(ipaddress.ip_network(c, strict=False))
        except ValueError:
            continue
    if not networks:
        return list(orig_addresses)

    matched = []
    has_dynamic = False
    for addr in orig_addresses:
        if not isinstance(addr, str):
            continue
        if addr.strip() == "dynamic":
            has_dynamic = True
            continue
        ip_obj = parse_peer_ip(addr)
        if ip_obj is not None:
            if any(ip_obj in net for net in networks):
                matched.append(addr)
        else:
            # Hostname-based address: keep it and let allowedNetworks guard the link.
            matched.append(addr)

    if has_dynamic or not matched:
        matched.append("dynamic")
    return matched


def reconcile_policies(client, store, state, net_info, local_id, options, cfg_devices, connections):
    """Apply user transport & bandwidth policies cleanly and idempotently."""
    state_dirty = False
    mode = state["transportMode"]
    remote_cfg_devices = [
        d for d in cfg_devices if is_valid_device_id(d.get("deviceID")) and d.get("deviceID") != local_id
    ]

    # Classify existing connected peers first.
    peer_net = {}
    for dev in remote_cfg_devices[:MAX_DEVICES]:
        dev_id = dev["deviceID"]
        conn = connections.get(dev_id, {})
        if conn.get("connected"):
            kind, peer_ip = connection_network_type(conn)
            peer_net[dev_id] = (kind, peer_ip)

    if mode in {"ethernet", "wifi"}:
        # Capture baseline user config before applying plugin transport overrides.
        if state["baselineListenAddresses"] is None:
            current_listen = options.get("listenAddresses")
            state["baselineListenAddresses"] = (
                list(current_listen) if isinstance(current_listen, list) and current_listen else ["default"]
            )
            state_dirty = True
        for dev in remote_cfg_devices:
            if save_device_baseline_if_missing(state, dev):
                state_dirty = True

        target_group = net_info[mode]
        ready_ifaces = target_group["readyInterfaces"]

        if ready_ifaces:
            desired_listen = build_listen_addresses(ready_ifaces)
            desired_cidrs = build_allowed_cidrs(ready_ifaces)
            patch_opts = {}
            if options.get("listenAddresses") != desired_listen:
                patch_opts["listenAddresses"] = desired_listen
            if not options.get("localAnnounceEnabled", True):
                patch_opts["localAnnounceEnabled"] = True
            if patch_opts:
                client.patch("config/options", patch_opts)
                options.update(patch_opts)

            baseline_addrs = state.get("baselineDeviceAddresses") or {}
            for dev in remote_cfg_devices:
                dev_id = dev["deviceID"]
                orig_addrs = baseline_addrs.get(dev_id) or list(dev.get("addresses") or ["dynamic"])
                desired_addrs = filter_device_addresses_for_cidrs(orig_addrs, desired_cidrs)
                dev_patch = {}
                if (dev.get("allowedNetworks") or []) != desired_cidrs:
                    dev_patch["allowedNetworks"] = desired_cidrs
                if (dev.get("addresses") or []) != desired_addrs:
                    dev_patch["addresses"] = desired_addrs
                if dev_patch:
                    client.patch(f"config/devices/{quote(dev_id, safe='')}", dev_patch)
                    dev.update(dev_patch)

            # Resume devices previously auto-paused by transport guard.
            if state["autoPaused"]:
                to_resume = state["autoPausedDevices"] or [
                    d["deviceID"] for d in remote_cfg_devices if not d.get("paused")
                ]
                for dev_id in to_resume:
                    client.post("system/resume?" + urlencode({"device": dev_id}))
                    if dev_id in connections:
                        connections[dev_id]["paused"] = False
                state["autoPaused"] = False
                state["autoPausedDevices"] = []
                state_dirty = True

            # Disconnect any peer currently connected over the wrong transport.
            for dev_id, (kind, _) in list(peer_net.items()):
                if kind != mode:
                    client.post("system/pause?" + urlencode({"device": dev_id}))
                    client.post("system/resume?" + urlencode({"device": dev_id}))
                    peer_net.pop(dev_id, None)
                    if dev_id in connections:
                        connections[dev_id]["connected"] = False
        else:
            # Required transport is unavailable: pause active remote devices so zero bytes leak over other links.
            if not state["autoPaused"]:
                newly_paused = []
                for dev in remote_cfg_devices:
                    dev_id = dev["deviceID"]
                    conn_paused = connections.get(dev_id, {}).get("paused", False)
                    cfg_paused = bool(dev.get("paused", False))
                    if not conn_paused and not cfg_paused:
                        client.post("system/pause?" + urlencode({"device": dev_id}))
                        newly_paused.append(dev_id)
                        if dev_id in connections:
                            connections[dev_id]["paused"] = True
                            connections[dev_id]["connected"] = False
                state["autoPaused"] = True
                state["autoPausedDevices"] = newly_paused
                peer_net.clear()
                state_dirty = True
    else:
        # Auto mode: restore global listenAddresses if previously pinned by LAN/Wi-Fi Only.
        if state["baselineListenAddresses"] is not None:
            restore_listen = state["baselineListenAddresses"] or ["default"]
            if options.get("listenAddresses") != restore_listen:
                client.patch("config/options", {"listenAddresses": restore_listen})
                options["listenAddresses"] = restore_listen
            state["baselineListenAddresses"] = None
            state_dirty = True

        if state["autoPaused"]:
            for dev_id in state["autoPausedDevices"]:
                client.post("system/resume?" + urlencode({"device": dev_id}))
                if dev_id in connections:
                    connections[dev_id]["paused"] = False
            state["autoPaused"] = False
            state["autoPausedDevices"] = []
            state_dirty = True

        # Smart LAN cable preference in Auto mode:
        # When Ethernet has carrier + IP and a peer is reachable on that Ethernet subnet,
        # pin that peer to Ethernet and reconnect off Wi-Fi; as soon as Ethernet is unplugged
        # or the peer is unreachable on Ethernet, restore its full Wi-Fi/dynamic baseline.
        eth_ready = net_info["ethernet"]["readyInterfaces"]
        eth_cidrs = build_allowed_cidrs(eth_ready) if eth_ready else []
        disco = None

        for dev in remote_cfg_devices:
            dev_id = dev["deviceID"]
            conn_info = connections.get(dev_id, {})
            if dev.get("paused") or conn_info.get("paused"):
                if restore_single_device_baseline(client, state, dev):
                    state_dirty = True
                continue

            current_kind = peer_net.get(dev_id, ("disconnected", ""))[0]
            if eth_cidrs and current_kind == "ethernet":
                continue

            saved_addrs = (state.get("baselineDeviceAddresses") or {}).get(dev_id)
            orig_addrs = saved_addrs if saved_addrs is not None else list(dev.get("addresses") or ["dynamic"])

            reachable_lan_ep = None
            if eth_cidrs:
                if disco is None:
                    try:
                        disco = client.get("system/discovery")
                    except Exception:
                        disco = {}
                for ip_str, port in peer_endpoints_in_cidrs(orig_addrs, disco, dev_id, eth_cidrs):
                    if can_reach_peer_tcp(ip_str, port):
                        reachable_lan_ep = (ip_str, port)
                        break

            if reachable_lan_ep is not None:
                if save_device_baseline_if_missing(state, dev):
                    state_dirty = True
                desired_addrs = filter_device_addresses_for_cidrs(orig_addrs, eth_cidrs)
                if desired_addrs == ["dynamic"]:
                    ip_s, port_s = reachable_lan_ep
                    rendered_ip = f"[{ip_s}]" if ":" in ip_s else ip_s
                    desired_addrs = [f"tcp://{rendered_ip}:{port_s}", "dynamic"]
                dev_patch = {}
                if (dev.get("allowedNetworks") or []) != eth_cidrs:
                    dev_patch["allowedNetworks"] = eth_cidrs
                if (dev.get("addresses") or []) != desired_addrs:
                    dev_patch["addresses"] = desired_addrs
                if dev_patch:
                    client.patch(f"config/devices/{quote(dev_id, safe='')}", dev_patch)
                    dev.update(dev_patch)
                if current_kind != "ethernet":
                    client.post("system/pause?" + urlencode({"device": dev_id}))
                    client.post("system/resume?" + urlencode({"device": dev_id}))
                    peer_net.pop(dev_id, None)
            else:
                was_pinned = restore_single_device_baseline(client, state, dev)
                if was_pinned:
                    state_dirty = True
                    if not conn_info.get("connected"):
                        client.post("system/pause?" + urlencode({"device": dev_id}))
                        client.post("system/resume?" + urlencode({"device": dev_id}))

    # Reconcile bandwidth limits (including "Limit speed on Wi-Fi only").
    active_kinds = {kind for kind, _ in peer_net.values()}
    cfg_recv = state["configuredRecvKbps"]
    cfg_send = state["configuredSendKbps"]
    has_limit = cfg_recv > 0 or cfg_send > 0
    bypass_on_lan = False

    if has_limit and state["limitWifiOnly"]:
        # Bypass speed cap whenever Ethernet is active and no peer is transferring over Wi-Fi.
        if "wifi" not in active_kinds and ("ethernet" in active_kinds or net_info["ethernet"]["available"]):
            bypass_on_lan = True

    desired_recv = 0 if bypass_on_lan else cfg_recv
    desired_send = 0 if bypass_on_lan else cfg_send
    bw_patch = {}
    if options.get("maxRecvKbps", 0) != desired_recv:
        bw_patch["maxRecvKbps"] = desired_recv
    if options.get("maxSendKbps", 0) != desired_send:
        bw_patch["maxSendKbps"] = desired_send
    if desired_recv > 0 or desired_send > 0:
        if not options.get("limitBandwidthInLan", False):
            bw_patch["limitBandwidthInLan"] = True
    if bw_patch:
        client.patch("config/options", bw_patch)
        options.update(bw_patch)

    if state_dirty:
        store.save(state)

    return peer_net, bypass_on_lan


def summarize(client, store):
    state = store.load()
    net_info = probe_interfaces()

    sys_status = client.get("system/status")
    local_id = sys_status.get("myID", "")
    cfg_folders = client.get("config/folders")
    cfg_devices = client.get("config/devices")
    options = client.get("config/options")
    traffic = client.get("system/connections")
    sampled_at = time.monotonic()

    connections = traffic.get("connections") or {}
    total = traffic.get("total") or {}

    # Sync state store with existing Syncthing limits on first run if untouched.
    if (
        state["bandwidthPreset"] == "unlimited"
        and state["configuredRecvKbps"] == 0
        and state["configuredSendKbps"] == 0
        and (options.get("maxRecvKbps", 0) > 0 or options.get("maxSendKbps", 0) > 0)
    ):
        cur_r = max(0, min(MAX_KBS, int(options.get("maxRecvKbps", 0) or 0)))
        cur_s = max(0, min(MAX_KBS, int(options.get("maxSendKbps", 0) or 0)))
        matched_preset = "custom"
        for name, (pr, ps) in BANDWIDTH_PRESETS.items():
            if (cur_r, cur_s) == (pr, ps):
                matched_preset = name
                break
        state["bandwidthPreset"] = matched_preset
        state["configuredRecvKbps"] = cur_r
        state["configuredSendKbps"] = cur_s
        store.save(state)

    peer_net, bypassed_on_lan = reconcile_policies(
        client, store, state, net_info, local_id, options, cfg_devices, connections
    )

    self_name = "Syncthing"
    remote_devices_cfg = []
    for dev in cfg_devices:
        if dev.get("deviceID") == local_id:
            label = sanitize_label(dev.get("name") or "")
            if label:
                self_name = label
        elif is_valid_device_id(dev.get("deviceID")):
            remote_devices_cfg.append(dev)

    shared = []
    for folder in cfg_folders:
        dev_ids = {
            d.get("deviceID")
            for d in (folder.get("devices") or [])
            if is_valid_device_id(d.get("deviceID")) and d.get("deviceID") != local_id
        }
        shared.append((folder, dev_ids))

    online_peers = {
        dev_id
        for dev_id, conn in connections.items()
        if conn.get("connected") and not conn.get("paused")
    }
    active_peer_ids = {peer for folder, dev_ids in shared if not folder.get("paused") for peer in dev_ids}
    active_online = active_peer_ids & online_peers
    active_networks = sorted({peer_net[p][0] for p in active_online if p in peer_net})

    # Folder status & completion calculation.
    folder_rows = []
    active_states = set()
    total_bytes_sum = 0
    need_bytes_sum = 0
    peer_need_bytes = {d["deviceID"]: 0 for d in remote_devices_cfg}
    peer_syncing = {d["deviceID"]: False for d in remote_devices_cfg}
    peer_completions = {d["deviceID"]: [] for d in remote_devices_cfg}

    for folder, dev_ids in shared:
        if not dev_ids:
            continue
        raw_fid = folder.get("id", "")
        safe_fid = raw_fid if is_valid_folder_id(raw_fid) else ""
        folder_paused = bool(folder.get("paused"))
        completion_pct = 100
        need_bytes = 0
        need_items = 0

        raw_path = folder.get("path", "")
        can_open = False
        if isinstance(raw_path, str) and raw_path.strip():
            expanded = Path(raw_path).expanduser()
            try:
                can_open = expanded.is_Absolute() if hasattr(expanded, "is_Absolute") else expanded.is_absolute()
                can_open = can_open and expanded.is_dir()
            except OSError:
                can_open = False

        if folder_paused:
            f_state = "paused"
        elif folder.get("invalid") or not safe_fid:
            f_state = "error"
        else:
            db_status = client.get("db/status?" + urlencode({"folder": safe_fid}))
            global_bytes = max(0, int(db_status.get("globalBytes", 0) or 0))
            need_bytes = max(0, int(db_status.get("needBytes", 0) or 0))
            need_items = max(0, int(db_status.get("needTotalItems", 0) or 0))
            total_bytes_sum += global_bytes
            need_bytes_sum += need_bytes
            if global_bytes > 0:
                completion_pct = max(0, min(100, int(round((global_bytes - need_bytes) * 100 / global_bytes))))

            if db_status.get("error") or db_status.get("pullErrors", 0):
                f_state = "error"
            elif db_status.get("state") in {"syncing", "sync-preparing"}:
                f_state = "syncing"
            else:
                connected_for_folder = dev_ids & online_peers
                f_state = "pending" if need_items > 0 else "ready"
                for peer in sorted(connected_for_folder)[:16]:
                    comp = client.get("db/completion?" + urlencode({"folder": safe_fid, "device": peer}))
                    p_comp = max(0, min(100, int(round(float(comp.get("completion", 100) or 100)))))
                    p_need = max(0, int(comp.get("needBytes", 0) or 0))
                    if peer in peer_completions:
                        peer_completions[peer].append(p_comp)
                        peer_need_bytes[peer] += p_need
                    if comp.get("remoteState") == "syncing":
                        f_state = "syncing"
                        if peer in peer_syncing:
                            peer_syncing[peer] = True
                    elif p_comp < 100 and f_state != "syncing":
                        f_state = "pending"
                        if p_comp < completion_pct:
                            completion_pct = p_comp
                if not connected_for_folder or (f_state == "ready" and connected_for_folder != dev_ids):
                    f_state = "offline"

        if f_state != "paused":
            active_states.add(f_state)

        if len(folder_rows) < MAX_FOLDERS:
            name = sanitize_label(folder.get("label") or safe_fid or "Folder")
            folder_rows.append(
                {
                    "id": safe_fid,
                    "name": name,
                    "state": f_state,
                    "paused": folder_paused,
                    "completion": completion_pct,
                    "needBytes": need_bytes,
                    "needItems": need_items,
                    "canOpen": can_open and bool(safe_fid),
                }
            )

    # Device summary list.
    device_rows = []
    all_remote_paused = bool(remote_devices_cfg)
    for dev in remote_devices_cfg[:MAX_DEVICES]:
        dev_id = dev["deviceID"]
        conn = connections.get(dev_id, {})
        is_paused = bool(dev.get("paused")) or bool(conn.get("paused"))
        if not is_paused:
            all_remote_paused = False
        is_connected = bool(conn.get("connected")) and not is_paused
        net_kind, peer_ip = peer_net.get(dev_id, ("disconnected", ""))
        if not is_connected:
            net_kind = "disconnected"
            peer_ip = ""
        comps = peer_completions.get(dev_id) or []
        dev_comp = min(comps) if comps else 100
        dev_name = sanitize_label(dev.get("name") or dev_id[:7])
        device_rows.append(
            {
                "id": dev_id,
                "name": dev_name,
                "connected": is_connected,
                "paused": is_paused,
                "network": net_kind,
                "address": sanitize_label(peer_ip, 48),
                "completion": dev_comp,
                "needBytes": peer_need_bytes.get(dev_id, 0),
                "syncing": bool(peer_syncing.get(dev_id, False)),
            }
        )

    # Overall status priority.
    if state["autoPaused"]:
        overall_status = "waiting_lan" if state["transportMode"] == "ethernet" else "waiting_wifi"
    elif all_remote_paused and not state["autoPaused"]:
        overall_status = "paused"
    else:
        overall_status = next(
            (s for s in ("error", "syncing", "pending", "offline", "ready") if s in active_states),
            "paused" if (shared and all(f.get("paused") for f, d in shared if d)) else "unconfigured",
        )

    overall_completion = 100
    if total_bytes_sum > 0 and need_bytes_sum > 0:
        overall_completion = max(0, min(99, int(round((total_bytes_sum - need_bytes_sum) * 100 / total_bytes_sum))))

    return {
        "ok": True,
        "status": overall_status,
        "paused": (all_remote_paused and not state["autoPaused"]),
        "autoPaused": state["autoPaused"],
        "selfName": self_name,
        "guiUrl": client.gui_url,
        "overallCompletion": overall_completion,
        "networks": active_networks,
        "transport": {
            "mode": state["transportMode"],
            "activeTypes": active_networks,
            "ethernet": {
                "available": net_info["ethernet"]["available"],
                "carrier": net_info["ethernet"]["carrier"],
                "interfaces": [
                    {
                        "name": i["name"],
                        "carrier": i["carrier"],
                        "ip": i["ip"],
                        "cidr": i["cidr"],
                        "linkLocalProfile": i["linkLocalProfile"],
                    }
                    for i in net_info["ethernet"]["interfaces"][:8]
                ],
            },
            "wifi": {
                "available": net_info["wifi"]["available"],
                "carrier": net_info["wifi"]["carrier"],
                "interfaces": [
                    {
                        "name": i["name"],
                        "carrier": i["carrier"],
                        "ip": i["ip"],
                        "cidr": i["cidr"],
                    }
                    for i in net_info["wifi"]["interfaces"][:8]
                ],
            },
            "cableWithoutIp": net_info["cableWithoutIp"],
            "directLinkLocalInterface": net_info["directLinkLocalInterface"],
        },
        "bandwidth": {
            "preset": state["bandwidthPreset"],
            "configuredRecvKbps": state["configuredRecvKbps"],
            "configuredSendKbps": state["configuredSendKbps"],
            "effectiveRecvKbps": int(options.get("maxRecvKbps", 0) or 0),
            "effectiveSendKbps": int(options.get("maxSendKbps", 0) or 0),
            "limitWifiOnly": state["limitWifiOnly"],
            "bypassedOnLan": bypassed_on_lan,
        },
        "traffic": {
            "received": max(0, int(total.get("inBytesTotal", 0) or 0)),
            "sent": max(0, int(total.get("outBytesTotal", 0) or 0)),
            "time": sampled_at,
        },
        "devices": device_rows,
        "folders": folder_rows,
        "hiddenFolders": max(0, sum(bool(devs) for _, devs in shared) - len(folder_rows)),
    }


def enable_link_local(iface):
    kind, _ = classify_sysfs_iface(iface)
    if kind != "ethernet":
        raise ControllerError("Selected interface is not a physical Ethernet device")
    if not Path("/usr/bin/nmcli").is_file():
        raise ControllerError("NetworkManager (nmcli) is not installed")

    # Check if our dedicated link-local profile already exists.
    show = subprocess.run(
        ["/usr/bin/nmcli", "-t", "-f", "NAME", "connection", "show", LINK_LOCAL_PROFILE],
        capture_output=True,
        text=True,
        check=False,
        timeout=2.0,
    )
    if show.returncode != 0:
        add = subprocess.run(
            [
                "/usr/bin/nmcli",
                "connection",
                "add",
                "type",
                "ethernet",
                "con-name",
                LINK_LOCAL_PROFILE,
                "ifname",
                iface,
                "ipv4.method",
                "link-local",
                "ipv6.method",
                "link-local",
                "connection.autoconnect",
                "no",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=3.0,
        )
        if add.returncode != 0:
            raise ControllerError(sanitize_label(add.stderr or add.stdout or "Failed to create Link-Local profile", 140))
    else:
        subprocess.run(
            [
                "/usr/bin/nmcli",
                "connection",
                "modify",
                LINK_LOCAL_PROFILE,
                "connection.interface-name",
                iface,
                "ipv4.method",
                "link-local",
                "ipv6.method",
                "link-local",
                "connection.autoconnect",
                "no",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )

    up = subprocess.run(
        ["/usr/bin/nmcli", "connection", "up", LINK_LOCAL_PROFILE, "ifname", iface],
        capture_output=True,
        text=True,
        check=False,
        timeout=4.0,
    )
    if up.returncode != 0:
        raise ControllerError(sanitize_label(up.stderr or up.stdout or "Failed to activate Link-Local connection", 140))

    # Give kernel IPv4 link-local assignment a brief moment to appear before summarizing.
    time.sleep(0.35)


def disable_link_local(iface):
    if not is_valid_iface_name(iface):
        raise ControllerError("Invalid network interface")
    if not Path("/usr/bin/nmcli").is_file():
        raise ControllerError("NetworkManager (nmcli) is not installed")

    subprocess.run(
        ["/usr/bin/nmcli", "connection", "down", LINK_LOCAL_PROFILE],
        capture_output=True,
        text=True,
        check=False,
        timeout=3.0,
    )
    subprocess.run(
        ["/usr/bin/nmcli", "device", "connect", iface],
        capture_output=True,
        text=True,
        check=False,
        timeout=3.0,
    )
    time.sleep(0.25)


def open_folder_safely(client, folder_id):
    if not is_valid_folder_id(folder_id):
        raise ControllerError("Invalid folder identifier")
    folder = client.get(f"config/folders/{quote(folder_id, safe='')}")
    raw_path = folder.get("path", "")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ControllerError("Folder path is empty")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_path):
        raise ControllerError("Folder path contains control characters")

    resolved = Path(raw_path).expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ControllerError("Folder directory does not exist locally")

    xdg_open = "/usr/bin/xdg-open" if Path("/usr/bin/xdg-open").is_file() else "xdg-open"
    subprocess.Popen(
        [xdg_open, str(resolved)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def parse_bool_arg(raw):
    if raw in {"1", "true", "True", "on", "yes"}:
        return True
    if raw in {"0", "false", "False", "off", "no"}:
        return False
    raise ControllerError("Invalid boolean flag")


def parse_kbps_arg(raw):
    try:
        val = int(raw, 10)
    except ValueError as exc:
        raise ControllerError("Invalid speed limit value") from exc
    if not (0 <= val <= MAX_KBS):
        raise ControllerError("Speed limit out of allowed range")
    return val


def dispatch(argv, client, store):
    cmd = argv[0] if argv else "status"

    if cmd == "status":
        if len(argv) > 1:
            raise ControllerError("Unexpected arguments for status")
        return summarize(client, store)

    if cmd == "set-transport":
        if len(argv) != 2 or argv[1] not in TRANSPORT_MODES:
            raise ControllerError("Invalid transport mode")
        state = store.load()
        state["transportMode"] = argv[1]
        store.save(state)
        return summarize(client, store)

    if cmd == "set-bandwidth":
        if len(argv) != 5:
            raise ControllerError("Invalid bandwidth arguments")
        preset = argv[1]
        if preset not in BANDWIDTH_PRESETS and preset != "custom":
            raise ControllerError("Unknown bandwidth preset")
        recv_kbps = parse_kbps_arg(argv[2])
        send_kbps = parse_kbps_arg(argv[3])
        wifi_only = parse_bool_arg(argv[4])
        if preset in BANDWIDTH_PRESETS:
            recv_kbps, send_kbps = BANDWIDTH_PRESETS[preset]
        state = store.load()
        state["bandwidthPreset"] = preset
        state["configuredRecvKbps"] = recv_kbps
        state["configuredSendKbps"] = send_kbps
        state["limitWifiOnly"] = wifi_only
        store.save(state)
        return summarize(client, store)

    if cmd == "pause-all":
        if len(argv) > 1:
            raise ControllerError("Unexpected arguments")
        state = store.load()
        state["autoPaused"] = False
        state["autoPausedDevices"] = []
        store.save(state)
        client.post("system/pause")
        return summarize(client, store)

    if cmd == "resume-all":
        if len(argv) > 1:
            raise ControllerError("Unexpected arguments")
        state = store.load()
        state["autoPaused"] = False
        state["autoPausedDevices"] = []
        store.save(state)
        client.post("system/resume")
        return summarize(client, store)

    if cmd == "set-device-paused":
        if len(argv) != 3 or not is_valid_device_id(argv[1]):
            raise ControllerError("Invalid device identifier")
        dev_id = argv[1]
        paused = parse_bool_arg(argv[2])
        endpoint = "system/pause" if paused else "system/resume"
        client.post(f"{endpoint}?" + urlencode({"device": dev_id}))
        return summarize(client, store)

    if cmd == "set-folder-paused":
        if len(argv) != 3 or not is_valid_folder_id(argv[1]):
            raise ControllerError("Invalid folder identifier")
        folder_id = argv[1]
        paused = parse_bool_arg(argv[2])
        client.patch(f"config/folders/{quote(folder_id, safe='')}", {"paused": paused})
        return summarize(client, store)

    if cmd == "rescan-folder":
        if len(argv) == 1 or (len(argv) == 2 and argv[1] == "--all"):
            client.post("db/scan")
            return summarize(client, store)
        if len(argv) != 2 or not is_valid_folder_id(argv[1]):
            raise ControllerError("Invalid folder identifier")
        client.post("db/scan?" + urlencode({"folder": argv[1]}))
        return summarize(client, store)

    if cmd == "open-folder":
        if len(argv) != 2:
            raise ControllerError("Missing folder identifier")
        open_folder_safely(client, argv[1])
        return {"ok": True, "opened": True}

    if cmd == "enable-link-local":
        if len(argv) != 2:
            raise ControllerError("Missing network interface")
        enable_link_local(argv[1])
        return summarize(client, store)

    if cmd == "disable-link-local":
        if len(argv) != 2:
            raise ControllerError("Missing network interface")
        disable_link_local(argv[1])
        return summarize(client, store)

    raise ControllerError("Unsupported command")


def _alarm_handler(*_):
    raise TimeoutError


def fallback_snapshot(store, status_name="unavailable"):
    try:
        state = store.load()
    except Exception:
        state = default_state()
    try:
        net_info = probe_interfaces()
    except Exception:
        net_info = {
            "ethernet": {"available": False, "carrier": False, "interfaces": []},
            "wifi": {"available": False, "carrier": False, "interfaces": []},
            "cableWithoutIp": None,
            "directLinkLocalInterface": "",
        }
    return {
        "ok": False,
        "status": status_name,
        "paused": False,
        "autoPaused": state["autoPaused"],
        "selfName": "Syncthing",
        "guiUrl": "http://127.0.0.1:8384/",
        "overallCompletion": 100,
        "networks": [],
        "transport": {
            "mode": state["transportMode"],
            "activeTypes": [],
            "ethernet": {
                "available": net_info["ethernet"]["available"],
                "carrier": net_info["ethernet"]["carrier"],
                "interfaces": [
                    {
                        "name": i["name"],
                        "carrier": i["carrier"],
                        "ip": i["ip"],
                        "cidr": i["cidr"],
                        "linkLocalProfile": i.get("linkLocalProfile", False),
                    }
                    for i in net_info["ethernet"]["interfaces"][:8]
                ],
            },
            "wifi": {
                "available": net_info["wifi"]["available"],
                "carrier": net_info["wifi"]["carrier"],
                "interfaces": [
                    {
                        "name": i["name"],
                        "carrier": i["carrier"],
                        "ip": i["ip"],
                        "cidr": i["cidr"],
                    }
                    for i in net_info["wifi"]["interfaces"][:8]
                ],
            },
            "cableWithoutIp": net_info["cableWithoutIp"],
            "directLinkLocalInterface": net_info["directLinkLocalInterface"],
        },
        "bandwidth": {
            "preset": state["bandwidthPreset"],
            "configuredRecvKbps": state["configuredRecvKbps"],
            "configuredSendKbps": state["configuredSendKbps"],
            "effectiveRecvKbps": 0,
            "effectiveSendKbps": 0,
            "limitWifiOnly": state["limitWifiOnly"],
            "bypassedOnLan": False,
        },
        "devices": [],
        "folders": [],
        "hiddenFolders": 0,
    }


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "status"
    alarm_seconds = 7 if cmd in {"enable-link-local", "disable-link-local"} else 5
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(alarm_seconds)

    store = StateStore()
    client = None
    try:
        # Persist transport and bandwidth choices immediately even if Syncthing is not running yet.
        if cmd == "set-transport":
            if len(args) != 2 or args[1] not in TRANSPORT_MODES:
                raise ControllerError("Invalid transport mode")
            state = store.load()
            state["transportMode"] = args[1]
            store.save(state)
            args = ["status"]
        elif cmd == "set-bandwidth":
            if len(args) != 5:
                raise ControllerError("Invalid bandwidth arguments")
            preset = args[1]
            if preset not in BANDWIDTH_PRESETS and preset != "custom":
                raise ControllerError("Unknown bandwidth preset")
            recv_kbps = parse_kbps_arg(args[2])
            send_kbps = parse_kbps_arg(args[3])
            wifi_only = parse_bool_arg(args[4])
            if preset in BANDWIDTH_PRESETS:
                recv_kbps, send_kbps = BANDWIDTH_PRESETS[preset]
            state = store.load()
            state["bandwidthPreset"] = preset
            state["configuredRecvKbps"] = recv_kbps
            state["configuredSendKbps"] = send_kbps
            state["limitWifiOnly"] = wifi_only
            store.save(state)
            args = ["status"]

        # Link-local network helper can run even before Syncthing is reachable, then summarize if available.
        if cmd in {"enable-link-local", "disable-link-local"}:
            if len(args) != 2:
                raise ControllerError("Missing network interface")
            if cmd == "enable-link-local":
                enable_link_local(args[1])
            else:
                disable_link_local(args[1])
            try:
                host, port, use_tls, key = discover_syncthing_endpoint()
                client = SyncthingClient(host, port, use_tls, key)
                print(json.dumps(summarize(client, store)))
            except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError):
                print(json.dumps(fallback_snapshot(store, "unavailable")))
            return 0

        host, port, use_tls, key = discover_syncthing_endpoint()
        client = SyncthingClient(host, port, use_tls, key)
        result = dispatch(args, client, store)
        print(json.dumps(result))
        return 0
    except ControllerError as exc:
        print(json.dumps({"ok": False, "status": "error", "actionError": sanitize_label(str(exc), 140)}))
        return 1
    except (FileNotFoundError, ConnectionRefusedError, TimeoutError):
        print(json.dumps(fallback_snapshot(store, "unavailable")))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ET.ParseError,
        http.client.HTTPException,
    ):
        print(json.dumps(fallback_snapshot(store, "error")))
        return 0
    finally:
        signal.alarm(0)
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
