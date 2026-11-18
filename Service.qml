import QtQuick
import Quickshell
import Quickshell.Io
import "Model.js" as Model

Item {
  id: root

  property var settings: ({})
  property bool panelOpen: false

  property string status: "unavailable"
  property string selfName: "Syncthing"
  property string guiUrl: "http://127.0.0.1:8384/"
  property bool paused: false
  property bool autoPaused: false
  property int overallCompletion: 100
  property var networks: []
  property var transport: ({
    mode: "auto",
    activeTypes: [],
    ethernet: { available: false, carrier: false, interfaces: [] },
    wifi: { available: false, carrier: false, interfaces: [] },
    cableWithoutIp: null,
    directLinkLocalInterface: ""
  })
  property var bandwidth: ({
    preset: "unlimited",
    configuredRecvKbps: 0,
    configuredSendKbps: 0,
    effectiveRecvKbps: 0,
    effectiveSendKbps: 0,
    limitWifiOnly: false,
    bypassedOnLan: false
  })
  property var devices: []
  property var folders: []
  property int hiddenFolders: 0

  property real downloadRate: -1
  property real uploadRate: -1
  property var speedHistory: Model.emptyHistory()
  property var _prevTraffic: null

  property string lastError: ""

  // Optimistic pending state so UI controls respond instantaneously.
  property string pendingTransportMode: ""
  property string pendingPreset: ""
  property int pendingRecvKbps: -1
  property int pendingSendKbps: -1
  property int pendingLimitWifiOnly: -1
  property int pendingGlobalPaused: -1

  readonly property string effectiveTransportMode: pendingTransportMode !== ""
    ? pendingTransportMode
    : String(transport.mode || "auto")
  readonly property string effectivePreset: pendingPreset !== ""
    ? pendingPreset
    : String(bandwidth.preset || "unlimited")
  readonly property int effectiveConfiguredRecvKbps: pendingRecvKbps >= 0
    ? pendingRecvKbps
    : Number(bandwidth.configuredRecvKbps || 0)
  readonly property int effectiveConfiguredSendKbps: pendingSendKbps >= 0
    ? pendingSendKbps
    : Number(bandwidth.configuredSendKbps || 0)
  readonly property bool effectiveLimitWifiOnly: pendingLimitWifiOnly >= 0
    ? (pendingLimitWifiOnly === 1)
    : (bandwidth.limitWifiOnly === true)
  readonly property bool effectivePaused: pendingGlobalPaused >= 0
    ? (pendingGlobalPaused === 1)
    : paused

  readonly property bool available: status !== "unavailable"
  readonly property bool active: available && !effectivePaused
  readonly property bool busy: actionProc.running
  readonly property string helperPath: decodeURIComponent(String(Qt.resolvedUrl("syncthing.py")).replace(/^file:\/\//, ""))

  property var _queuedActionArgs: null

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  function intSetting(name, fallback, min, max) {
    var n = parseInt(String(setting(name, fallback)), 10)
    if (!isFinite(n)) n = fallback
    if (n < min) n = min
    if (n > max) n = max
    return n
  }

  readonly property int openRefreshMs: intSetting("openRefreshSec", 1, 1, 10) * 1000
  readonly property int closedRefreshMs: intSetting("closedRefreshSec", 5, 2, 60) * 1000

  function refresh() {
    if (probeProc.running || actionProc.running) return
    probeProc.command = ["/usr/bin/python3", "-I", "-B", helperPath, "status"]
    probeProc.running = true
  }

  function enqueueAction(args) {
    if (!Array.isArray(args) || args.length === 0) return
    if (actionProc.running) {
      _queuedActionArgs = args
      return
    }
    actionProc.command = ["/usr/bin/python3", "-I", "-B", helperPath].concat(args)
    actionProc.running = true
  }

  function clearOptimisticOverrides() {
    pendingTransportMode = ""
    pendingPreset = ""
    pendingRecvKbps = -1
    pendingSendKbps = -1
    pendingLimitWifiOnly = -1
    pendingGlobalPaused = -1
  }

  function applySnapshot(parsed) {
    if (parsed.actionError) {
      lastError = parsed.actionError
      clearOptimisticOverrides()
      return
    }

    var current = parsed.traffic
    var previous = _prevTraffic
    if (current && previous && typeof current.time === "number" && typeof previous.time === "number") {
      var elapsed = current.time - previous.time
      var valid = elapsed >= 0.2 && elapsed <= 20
        && current.received >= previous.received
        && current.sent >= previous.sent
      if (valid) {
        downloadRate = (current.received - previous.received) / elapsed
        uploadRate = (current.sent - previous.sent) / elapsed
        speedHistory = Model.pushSpeedSample(speedHistory, downloadRate, uploadRate)
      } else {
        downloadRate = -1
        uploadRate = -1
      }
    } else if (!current) {
      downloadRate = -1
      uploadRate = -1
    }
    if (current) _prevTraffic = current

    status = parsed.status
    selfName = parsed.selfName
    guiUrl = parsed.guiUrl
    paused = parsed.paused
    autoPaused = parsed.autoPaused
    overallCompletion = parsed.overallCompletion
    networks = parsed.networks
    transport = parsed.transport
    bandwidth = parsed.bandwidth

    if (JSON.stringify(devices) !== JSON.stringify(parsed.devices)) devices = parsed.devices
    if (JSON.stringify(folders) !== JSON.stringify(parsed.folders)) folders = parsed.folders
    hiddenFolders = parsed.hiddenFolders

    if (!actionProc.running && _queuedActionArgs === null) {
      clearOptimisticOverrides()
    }
  }

  function setTransportMode(mode) {
    if (mode !== "auto" && mode !== "ethernet" && mode !== "wifi") return
    lastError = ""
    pendingTransportMode = mode
    enqueueAction(["set-transport", mode])
  }

  function setBandwidthPreset(preset) {
    lastError = ""
    pendingPreset = preset
    var recv = effectiveConfiguredRecvKbps
    var send = effectiveConfiguredSendKbps
    if (preset === "unlimited") { recv = 0; send = 0 }
    else if (preset === "25m") { recv = 25600; send = 25600 }
    else if (preset === "10m") { recv = 10240; send = 10240 }
    else if (preset === "2m") { recv = 2048; send = 2048 }
    else if (preset === "custom") {
      if (recv <= 0) recv = 10240
      if (send <= 0) send = 10240
    }
    pendingRecvKbps = recv
    pendingSendKbps = send
    enqueueAction([
      "set-bandwidth",
      preset,
      String(recv),
      String(send),
      effectiveLimitWifiOnly ? "1" : "0"
    ])
  }

  function setCustomBandwidth(recvKbps, sendKbps) {
    var r = Math.max(0, Math.min(10485760, Math.round(Number(recvKbps) || 0)))
    var s = Math.max(0, Math.min(10485760, Math.round(Number(sendKbps) || 0)))
    lastError = ""
    pendingPreset = "custom"
    pendingRecvKbps = r
    pendingSendKbps = s
    enqueueAction([
      "set-bandwidth",
      "custom",
      String(r),
      String(s),
      effectiveLimitWifiOnly ? "1" : "0"
    ])
  }

  function toggleLimitWifiOnly() {
    var next = !effectiveLimitWifiOnly
    lastError = ""
    pendingLimitWifiOnly = next ? 1 : 0
    enqueueAction([
      "set-bandwidth",
      effectivePreset,
      String(effectiveConfiguredRecvKbps),
      String(effectiveConfiguredSendKbps),
      next ? "1" : "0"
    ])
  }

  function toggleGlobalPause() {
    if (!available) return
    lastError = ""
    if (effectivePaused || autoPaused) {
      pendingGlobalPaused = 0
      enqueueAction(["resume-all"])
    } else {
      pendingGlobalPaused = 1
      enqueueAction(["pause-all"])
    }
  }

  function pauseAll() {
    if (!available) return
    lastError = ""
    pendingGlobalPaused = 1
    enqueueAction(["pause-all"])
  }

  function resumeAll() {
    if (!available) return
    lastError = ""
    pendingGlobalPaused = 0
    enqueueAction(["resume-all"])
  }

  function setDevicePaused(deviceId, shouldPause) {
    if (!deviceId) return
    lastError = ""
    enqueueAction(["set-device-paused", String(deviceId), shouldPause ? "1" : "0"])
  }

  function setFolderPaused(folderId, shouldPause) {
    if (!folderId) return
    lastError = ""
    enqueueAction(["set-folder-paused", String(folderId), shouldPause ? "1" : "0"])
  }

  function rescanFolder(folderId) {
    if (!folderId) return
    lastError = ""
    enqueueAction(["rescan-folder", String(folderId)])
  }

  function rescanAll() {
    if (!available) return
    lastError = ""
    enqueueAction(["rescan-folder", "--all"])
  }

  function openFolder(folderId) {
    if (!folderId) return
    lastError = ""
    enqueueAction(["open-folder", String(folderId)])
  }

  function enableDirectCable(ifaceName) {
    if (!ifaceName) return
    lastError = ""
    enqueueAction(["enable-link-local", String(ifaceName)])
  }

  function restoreDhcpCable(ifaceName) {
    if (!ifaceName) return
    lastError = ""
    enqueueAction(["disable-link-local", String(ifaceName)])
  }

  Timer {
    id: pollTimer
    interval: (root.panelOpen || root.status === "syncing") ? root.openRefreshMs : root.closedRefreshMs
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Process {
    id: probeProc
    stdout: StdioCollector {
      id: probeStdout
      waitForEnd: true
    }
    onExited: function(exitCode) {
      if (actionProc.running) return
      var parsed = Model.parseSnapshot(probeStdout.text)
      if (exitCode === 0 && parsed.status) {
        root.applySnapshot(parsed)
      } else {
        root.applySnapshot({ ok: false, status: "unavailable" })
      }
    }
  }

  Process {
    id: actionProc
    stdout: StdioCollector {
      id: actionStdout
      waitForEnd: true
    }
    onExited: function(exitCode) {
      var text = String(actionStdout.text || "").trim()
      if (text !== "") {
        try {
          var rawObj = JSON.parse(text)
          if (rawObj && rawObj.opened === true) {
            // open-folder returns { ok: true, opened: true }; keep current snapshot.
          } else {
            var parsed = Model.parseSnapshot(text)
            root.applySnapshot(parsed)
          }
        } catch (e) {
          root.clearOptimisticOverrides()
        }
      } else {
        root.clearOptimisticOverrides()
      }

      if (root._queuedActionArgs !== null) {
        var nextArgs = root._queuedActionArgs
        root._queuedActionArgs = null
        root.enqueueAction(nextArgs)
      }
    }
  }
}
