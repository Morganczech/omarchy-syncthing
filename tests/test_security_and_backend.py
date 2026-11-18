#!/usr/bin/env python3
"""Security, policy, i18n, and QML safety regression suite for gruut.syncthing."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import syncthing  # noqa: E402

LOCAL_ID = "AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA-AAAAAAA"
PEER_NOTEBOOK = "BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB-BBBBBBB"
PEER_MANUAL_PAUSED = "CCCCCCC-CCCCCCC-CCCCCCC-CCCCCCC-CCCCCCC-CCCCCCC-CCCCCCC-CCCCCCC"
SECRET_KEY = "SecretApiKey_DoNotLeak_987654321"


class FakeSyncthingServer:
    """In-memory mock of Syncthing's loopback REST API."""

    def __init__(self, folder_path):
        self.options = {
            "listenAddresses": ["default"],
            "localAnnounceEnabled": True,
            "maxRecvKbps": 0,
            "maxSendKbps": 0,
            "limitBandwidthInLan": False,
        }
        self.devices = [
            {"deviceID": LOCAL_ID, "name": "omarchy-pc", "paused": False, "allowedNetworks": [], "addresses": ["dynamic"]},
            {
                "deviceID": PEER_NOTEBOOK,
                "name": "notebook <img src=x>",
                "paused": False,
                "allowedNetworks": [],
                "addresses": ["tcp://169.254.10.99:22000", "tcp://192.168.1.105:22000", "dynamic"],
            },
            {
                "deviceID": PEER_MANUAL_PAUSED,
                "name": "archive-nas",
                "paused": True,
                "allowedNetworks": ["10.0.0.0/8"],
                "addresses": ["dynamic"],
            },
        ]
        self.folders = [
            {
                "id": "work-docs",
                "label": "Work\nDocuments\x07",
                "path": str(folder_path),
                "paused": False,
                "devices": [{"deviceID": LOCAL_ID}, {"deviceID": PEER_NOTEBOOK}],
            }
        ]
        self.connections = {
            PEER_NOTEBOOK: {
                "address": "169.254.10.99:22000",
                "connected": True,
                "paused": False,
                "inBytesTotal": 1048576,
                "outBytesTotal": 524288,
            },
            PEER_MANUAL_PAUSED: {
                "address": "",
                "connected": False,
                "paused": True,
                "inBytesTotal": 0,
                "outBytesTotal": 0,
            },
        }
        self.calls = []
        state_ref = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _check_auth(self):
                if self.headers.get("X-API-Key") != SECRET_KEY:
                    self.send_response(403)
                    self.end_headers()
                    return False
                return True

            def _send_json(self, obj, code=200):
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if not self._check_auth():
                    return
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                state_ref.calls.append(("GET", parsed.path))

                if parsed.path == "/rest/system/status":
                    self._send_json({"myID": LOCAL_ID})
                elif parsed.path == "/rest/config/options":
                    self._send_json(state_ref.options)
                elif parsed.path == "/rest/config/devices":
                    self._send_json(state_ref.devices)
                elif parsed.path == "/rest/config/folders":
                    self._send_json(state_ref.folders)
                elif parsed.path.startswith("/rest/config/folders/"):
                    fid = unquote(parsed.path.rsplit("/", 1)[1])
                    for f in state_ref.folders:
                        if f["id"] == fid:
                            self._send_json(f)
                            return
                    self._send_json({}, 404)
                elif parsed.path == "/rest/system/connections":
                    self._send_json(
                        {
                            "total": {"inBytesTotal": 2097152, "outBytesTotal": 1048576},
                            "connections": state_ref.connections,
                        }
                    )
                elif parsed.path == "/rest/system/discovery":
                    self._send_json({PEER_NOTEBOOK: {"addresses": ["tcp://169.254.10.99:22000"]}})
                elif parsed.path == "/rest/db/status":
                    self._send_json(
                        {
                            "state": "idle",
                            "globalBytes": 10000,
                            "needBytes": 0,
                            "needTotalItems": 0,
                            "pullErrors": 0,
                        }
                    )
                elif parsed.path == "/rest/db/completion":
                    self._send_json({"completion": 100, "needBytes": 0, "remoteState": "idle"})
                else:
                    self._send_json({}, 404)

            def do_PATCH(self):
                if not self._check_auth():
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
                parsed = urlparse(self.path)
                state_ref.calls.append(("PATCH", parsed.path, body))

                if parsed.path == "/rest/config/options":
                    state_ref.options.update(body)
                    self._send_json(state_ref.options)
                elif parsed.path.startswith("/rest/config/devices/"):
                    dev_id = unquote(parsed.path.rsplit("/", 1)[1])
                    for d in state_ref.devices:
                        if d["deviceID"] == dev_id:
                            d.update(body)
                            self._send_json(d)
                            return
                    self._send_json({}, 404)
                elif parsed.path.startswith("/rest/config/folders/"):
                    fid = unquote(parsed.path.rsplit("/", 1)[1])
                    for f in state_ref.folders:
                        if f["id"] == fid:
                            f.update(body)
                            self._send_json(f)
                            return
                    self._send_json({}, 404)
                else:
                    self._send_json({}, 404)

            def do_POST(self):
                if not self._check_auth():
                    return
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                dev_id = qs.get("device", [None])[0]
                state_ref.calls.append(("POST", parsed.path, dev_id))

                if parsed.path == "/rest/system/pause":
                    targets = [dev_id] if dev_id else list(state_ref.connections.keys())
                    for tid in targets:
                        if tid in state_ref.connections:
                            state_ref.connections[tid]["paused"] = True
                            state_ref.connections[tid]["connected"] = False
                    self.send_response(200)
                    self.end_headers()
                elif parsed.path == "/rest/system/resume":
                    targets = [dev_id] if dev_id else list(state_ref.connections.keys())
                    for tid in targets:
                        if tid in state_ref.connections:
                            state_ref.connections[tid]["paused"] = False
                    self.send_response(200)
                    self.end_headers()
                elif parsed.path == "/rest/db/scan":
                    self.send_response(200)
                    self.end_headers()
                else:
                    self._send_json({}, 404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class SecurityAndBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="syncthing-plugin-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_xml_rejects_xxe_oversized_and_non_loopback(self):
        xxe_xml = (
            b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b"<configuration><gui><address>127.0.0.1:8384</address><apikey>abc123</apikey></gui></configuration>"
        )
        with self.assertRaises(ValueError):
            syncthing.parse_loopback_gui(xxe_xml)

        with self.assertRaises(ValueError):
            syncthing.parse_loopback_gui(b"x" * (syncthing.MAX_XML_BYTES + 5))

        exposed_xml = (
            b"<configuration><gui><address>0.0.0.0:8384</address>"
            b"<apikey>ValidKey123</apikey></gui></configuration>"
        )
        with self.assertRaises(ValueError):
            syncthing.parse_loopback_gui(exposed_xml)

        bad_key_xml = (
            b"<configuration><gui><address>127.0.0.1:8384</address>"
            b"<apikey>bad key; rm -rf /</apikey></gui></configuration>"
        )
        with self.assertRaises(ValueError):
            syncthing.parse_loopback_gui(bad_key_xml)

        valid_tls_xml = (
            b'<configuration><gui tls="true"><address>[::1]:8384</address>'
            b"<apikey>Safe_Key-123</apikey></gui></configuration>"
        )
        host, port, use_tls, key = syncthing.parse_loopback_gui(valid_tls_xml)
        self.assertEqual((host, port, use_tls, key), ("::1", 8384, True, "Safe_Key-123"))

    def test_state_store_rejects_symlinks_and_enforces_0600(self):
        victim = self.base / "victim.txt"
        victim.write_text("do-not-touch", encoding="utf-8")

        store_dir = self.base / "state"
        store = syncthing.StateStore(base_dir=store_dir)
        store.save({"transportMode": "ethernet", "injectedField": "dropped"})

        dir_mode = stat.S_IMODE(os.lstat(store_dir).st_mode)
        file_mode = stat.S_IMODE(os.lstat(store.path).st_mode)
        self.assertEqual(dir_mode, 0o700)
        self.assertEqual(file_mode, 0o600)

        loaded = store.load()
        self.assertEqual(loaded["transportMode"], "ethernet")
        self.assertNotIn("injectedField", loaded)

        # Replace state.json with a symlink to victim and ensure neither load nor save follows/clobbers it.
        store.path.unlink()
        store.path.symlink_to(victim)
        with self.assertRaises(syncthing.ControllerError):
            store.load()
        with self.assertRaises(syncthing.ControllerError):
            store.save({"transportMode": "wifi"})
        self.assertEqual(victim.read_text(encoding="utf-8"), "do-not-touch")

    def test_strict_identifier_and_sysfs_validation(self):
        self.assertTrue(syncthing.is_valid_device_id(PEER_NOTEBOOK))
        self.assertFalse(syncthing.is_valid_device_id("../../etc/passwd"))
        self.assertFalse(syncthing.is_valid_device_id(PEER_NOTEBOOK + "&foo=bar"))

        self.assertTrue(syncthing.is_valid_folder_id("default_folder-1.2"))
        self.assertFalse(syncthing.is_valid_folder_id("../secret"))
        self.assertFalse(syncthing.is_valid_folder_id("folder/sub"))
        self.assertFalse(syncthing.is_valid_folder_id("folder?x=1"))

        fake_sys = self.base / "sys_net"
        (fake_sys / "enp4s0" / "device").mkdir(parents=True)
        (fake_sys / "enp4s0" / "type").write_text("1\n", encoding="utf-8")
        (fake_sys / "enp4s0" / "carrier").write_text("1\n", encoding="utf-8")

        (fake_sys / "wlan0" / "wireless").mkdir(parents=True)
        (fake_sys / "wlan0" / "carrier").write_text("0\n", encoding="utf-8")

        (fake_sys / "docker0" / "device").mkdir(parents=True)
        (fake_sys / "docker0" / "type").write_text("1\n", encoding="utf-8")

        self.assertEqual(syncthing.classify_sysfs_iface("enp4s0", fake_sys), ("ethernet", True))
        self.assertEqual(syncthing.classify_sysfs_iface("wlan0", fake_sys), ("wifi", False))
        self.assertEqual(syncthing.classify_sysfs_iface("docker0", fake_sys), ("unknown", False))
        self.assertEqual(syncthing.classify_sysfs_iface("../enp4s0", fake_sys), ("unknown", False))

    def test_transport_and_bandwidth_policy_lifecycle(self):
        shared_dir = self.base / "SyncedDocs"
        shared_dir.mkdir()
        server = FakeSyncthingServer(shared_dir)
        self.addCleanup(server.close)

        store = syncthing.StateStore(base_dir=self.base / "plugin-state")
        client = syncthing.SyncthingClient("127.0.0.1", server.port, False, SECRET_KEY)
        self.addCleanup(client.close)

        eth_ready_net = {
            "ethernet": {
                "available": True,
                "carrier": True,
                "interfaces": [
                    {
                        "name": "enp4s0",
                        "carrier": True,
                        "ipv4": ["169.254.10.2"],
                        "ipv6": [],
                        "cidrs": ["169.254.0.0/16"],
                        "ip": "169.254.10.2",
                        "cidr": "169.254.0.0/16",
                        "linkLocalProfile": True,
                    }
                ],
                "readyInterfaces": [
                    {
                        "name": "enp4s0",
                        "carrier": True,
                        "ipv4": ["169.254.10.2"],
                        "ipv6": [],
                        "cidrs": ["169.254.0.0/16"],
                        "ip": "169.254.10.2",
                        "cidr": "169.254.0.0/16",
                        "linkLocalProfile": True,
                    }
                ],
            },
            "wifi": {"available": False, "carrier": False, "interfaces": [], "readyInterfaces": []},
            "cableWithoutIp": None,
            "directLinkLocalInterface": "enp4s0",
        }

        eth_unplugged_net = {
            "ethernet": {"available": False, "carrier": False, "interfaces": [], "readyInterfaces": []},
            "wifi": {"available": True, "carrier": True, "interfaces": [], "readyInterfaces": []},
            "cableWithoutIp": None,
            "directLinkLocalInterface": "",
        }

        orig_probe = syncthing.probe_interfaces
        orig_conn_type = syncthing.connection_network_type
        current_net = [eth_ready_net]
        current_peer_kind = ["ethernet"]
        syncthing.probe_interfaces = lambda: current_net[0]
        syncthing.connection_network_type = lambda conn: (current_peer_kind[0], "169.254.10.99")
        try:
            # 1. Pin to LAN Only while LAN cable is connected.
            snap = syncthing.dispatch(["set-transport", "ethernet"], client, store)
            self.assertNotIn(SECRET_KEY, json.dumps(snap))
            self.assertEqual(snap["transport"]["mode"], "ethernet")
            self.assertEqual(
                server.options["listenAddresses"],
                ["tcp4://169.254.10.2:22000", "quic4://169.254.10.2:22000"],
            )
            notebook_cfg = next(d for d in server.devices if d["deviceID"] == PEER_NOTEBOOK)
            self.assertEqual(notebook_cfg["allowedNetworks"], ["169.254.0.0/16"])

            # 2. Configure 10 MiB/s limit with Wi-Fi-only flag: should bypass to 0 on Ethernet!
            snap = syncthing.dispatch(["set-bandwidth", "10m", "10240", "10240", "1"], client, store)
            self.assertTrue(snap["bandwidth"]["bypassedOnLan"])
            self.assertEqual(server.options["maxRecvKbps"], 0)
            self.assertEqual(server.options["maxSendKbps"], 0)

            # 3. Unplug LAN cable while pinned to LAN Only -> auto-pauses active peer, never leaks to Wi-Fi!
            current_net[0] = eth_unplugged_net
            snap = syncthing.dispatch(["status"], client, store)
            self.assertEqual(snap["status"], "waiting_lan")
            self.assertTrue(snap["autoPaused"])
            self.assertTrue(server.connections[PEER_NOTEBOOK]["paused"])

            # 4. Switch back to Auto on Wi-Fi -> restores baseline listenAddresses, allowedNetworks, and addresses,
            # resumes PEER_NOTEBOOK, leaves PEER_MANUAL_PAUSED paused, and enforces 10 MiB/s Wi-Fi cap!
            server.connections[PEER_NOTEBOOK]["connected"] = True
            current_peer_kind[0] = "wifi"
            snap = syncthing.dispatch(["set-transport", "auto"], client, store)
            self.assertEqual(server.options["listenAddresses"], ["default"])
            self.assertEqual(notebook_cfg["allowedNetworks"], [])
            self.assertEqual(
                notebook_cfg["addresses"],
                ["tcp://169.254.10.99:22000", "tcp://192.168.1.105:22000", "dynamic"],
            )
            manual_cfg = next(d for d in server.devices if d["deviceID"] == PEER_MANUAL_PAUSED)
            self.assertEqual(manual_cfg["allowedNetworks"], ["10.0.0.0/8"])
            self.assertFalse(server.connections[PEER_NOTEBOOK]["paused"])
            self.assertTrue(server.connections[PEER_MANUAL_PAUSED]["paused"])
            self.assertFalse(snap["bandwidth"]["bypassedOnLan"])
            self.assertEqual(server.options["maxRecvKbps"], 10240)
            self.assertTrue(server.options["limitBandwidthInLan"])

            # 4b. Plug LAN cable back in while in Auto mode and peer is reachable over TCP on LAN ->
            # automatically pins peer onto LAN address/subnet and bypasses Wi-Fi bandwidth cap!
            orig_reach = syncthing.can_reach_peer_tcp
            syncthing.can_reach_peer_tcp = lambda ip_s, port_s, timeout=0.35: ip_s == "169.254.10.99"
            try:
                current_net[0] = eth_ready_net
                snap = syncthing.dispatch(["status"], client, store)
                self.assertEqual(notebook_cfg["allowedNetworks"], ["169.254.0.0/16"])
                self.assertEqual(notebook_cfg["addresses"], ["tcp://169.254.10.99:22000", "dynamic"])
                self.assertTrue(snap["bandwidth"]["bypassedOnLan"])
                self.assertEqual(server.options["maxRecvKbps"], 0)
            finally:
                syncthing.can_reach_peer_tcp = orig_reach

            # 5. Reject malicious CLI arguments.
            for bad_args in (
                ["set-transport", "cellular"],
                ["set-bandwidth", "custom", "-5", "100", "0"],
                ["set-device-paused", "bad-id", "1"],
                ["set-folder-paused", "../escape", "1"],
                ["unknown-cmd"],
            ):
                with self.assertRaises(syncthing.ControllerError):
                    syncthing.dispatch(bad_args, client, store)
        finally:
            syncthing.probe_interfaces = orig_probe
            syncthing.connection_network_type = orig_conn_type

    def test_qml_plain_text_and_i18n_catalog_completeness(self):
        en = json.loads((ROOT / "i18n" / "en.json").read_text(encoding="utf-8"))
        self.assertGreater(len(en), 30)

        panel_src = (ROOT / "Panel.qml").read_text(encoding="utf-8")
        literal_keys = set(re.findall(r'strings\.t\("([^"+]+)"\)', panel_src))
        for key in literal_keys:
            self.assertIn(key, en, f"Missing key in en.json: {key}")

        # Verify every QML Text item explicitly declares textFormat: Text.PlainText.
        for qml_file in sorted(ROOT.glob("*.qml")):
            content = qml_file.read_text(encoding="utf-8")
            for match in re.finditer(r"\bText\s*\{", content):
                start = match.end()
                depth = 1
                idx = start
                while idx < len(content) and depth > 0:
                    if content[idx] == "{":
                        depth += 1
                    elif content[idx] == "}":
                        depth -= 1
                    idx += 1
                block = content[start:idx]
                self.assertIn(
                    "textFormat: Text.PlainText",
                    block,
                    f"Missing textFormat: Text.PlainText in {qml_file.name}",
                )

    def test_model_js_sanitization_and_helpers(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        js_src = (ROOT / "Model.js").read_text(encoding="utf-8").replace(".pragma library", "")
        harness = (
            js_src
            + r"""
const assert = require("assert");
assert.strictEqual(neutralizeMarkup('<img src="https://evil.invalid/x">&\n'), '‹img src="https://evil.invalid/x"›＆');
assert.strictEqual(safeGuiUrl('javascript:alert(1)'), 'http://127.0.0.1:8384/');
assert.strictEqual(safeGuiUrl('https://127.0.0.1:8384/'), 'https://127.0.0.1:8384/');
assert.strictEqual(sliderIndexToKbps(kbpsToSliderIndex(10240)), 10240);
let h = emptyHistory();
assert.strictEqual(h.length, 60);
h = pushSpeedSample(h, 52428800, 1048576);
assert.strictEqual(h.length, 60);
assert.strictEqual(historyPeaks(h).rx, 52428800);
"""
        )
        proc = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_qmllint_against_omarchy_shell(self):
        shell = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
        lint = "/usr/lib/qt6/bin/qmllint"
        if not Path(lint).is_file():
            lint = shutil.which("qmllint") or ""
        if not (shell / "Ui" / "qmldir").is_file() or not lint:
            self.skipTest("qmllint or Omarchy shell sources not available")

        imports_dir = self.base / "qml_imports"
        (imports_dir / "qs").mkdir(parents=True)
        for mod in ("Commons", "Ui"):
            (imports_dir / "qs" / mod).symlink_to(shell / mod, target_is_directory=True)

        qml_files = sorted(str(p) for p in ROOT.glob("*.qml"))
        res = subprocess.run(
            [lint, "-I", str(imports_dir), "--json", "-", *qml_files],
            capture_output=True,
            text=True,
            timeout=30,
        )
        report = json.loads(res.stdout)
        failures = [
            f'{entry["filename"]}:{issue.get("line", 0)}: [{issue.get("id")}] {issue["message"]}'
            for entry in report.get("files", [])
            for issue in entry.get("warnings", [])
            if issue.get("type") == "error"
            or issue.get("id") in ("import", "unresolved-type", "inheritance-cycle", "required", "syntax")
        ]
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
