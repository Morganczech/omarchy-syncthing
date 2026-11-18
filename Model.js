.pragma library

var HISTORY_LENGTH = 60

var SPEED_STOPS_KBPS = [
  128,
  256,
  512,
  1024,
  2048,
  4096,
  6144,
  8192,
  10240,
  15360,
  20480,
  25600,
  35840,
  51200,
  76800,
  102400
]

var GRAPH_SCALE_STOPS = [
  65536,       // 64 KiB/s
  262144,      // 256 KiB/s
  1048576,     // 1 MiB/s
  5242880,     // 5 MiB/s
  10485760,    // 10 MiB/s
  26214400,    // 25 MiB/s
  52428800,    // 50 MiB/s
  104857600,   // 100 MiB/s
  262144000,   // 250 MiB/s
  524288000,   // 500 MiB/s
  1073741824   // 1 GiB/s
]

var VALID_STATUSES = {
  unavailable: true,
  error: true,
  unconfigured: true,
  offline: true,
  pending: true,
  syncing: true,
  ready: true,
  paused: true,
  waiting_lan: true,
  waiting_wifi: true
}

function plainText(value, maxLen) {
  var limit = (typeof maxLen === "number" && maxLen > 0) ? maxLen : 120
  var raw = String(value === undefined || value === null ? "" : value)
  var cleaned = raw.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim()
  if (cleaned.length > limit) return cleaned.substring(0, limit - 1) + "\u2026"
  return cleaned
}

// Neutralize angle-bracket markup before handing untrusted strings to any
// shared shell surface whose internal Text.textFormat is outside plugin control.
function neutralizeMarkup(value, maxLen) {
  var safe = plainText(value, maxLen)
  return safe.replace(/</g, "\u2039").replace(/>/g, "\u203a").replace(/&/g, "\uff06")
}

function defaultFormatNumber(value, decimals) {
  var n = Number(value)
  if (!isFinite(n)) return "0"
  var d = (typeof decimals === "number" && decimals >= 0) ? decimals : 0
  return n.toFixed(d)
}

function formatRate(bytesPerSec, formatNumber) {
  var fmt = typeof formatNumber === "function" ? formatNumber : defaultFormatNumber
  var rate = Number(bytesPerSec)
  if (!isFinite(rate) || rate < 0) return "\u2014"
  if (rate >= 1048576) {
    return fmt(rate / 1048576, rate >= 104857600 ? 0 : 1) + " MiB/s"
  }
  if (rate >= 1024) {
    return fmt(rate / 1024, rate >= 102400 ? 0 : 1) + " KiB/s"
  }
  return fmt(Math.round(rate), 0) + " B/s"
}

function formatCompactRate(bytesPerSec, formatNumber) {
  var fmt = typeof formatNumber === "function" ? formatNumber : defaultFormatNumber
  var rate = Number(bytesPerSec)
  if (!isFinite(rate) || rate < 0) return "0B"
  if (rate >= 1048576) return fmt(rate / 1048576, rate >= 10485760 ? 0 : 1) + "M"
  if (rate >= 1024) return fmt(rate / 1024, 0) + "K"
  return fmt(Math.round(rate), 0) + "B"
}

function formatBytes(bytes, formatNumber) {
  var fmt = typeof formatNumber === "function" ? formatNumber : defaultFormatNumber
  var b = Math.max(0, Number(bytes) || 0)
  if (b >= 1073741824) return fmt(b / 1073741824, 1) + " GiB"
  if (b >= 1048576) return fmt(b / 1048576, 1) + " MiB"
  if (b >= 1024) return fmt(b / 1024, 0) + " KiB"
  return fmt(Math.round(b), 0) + " B"
}

function formatKbpsLimit(kbps, formatNumber, unlimitedLabel) {
  var fmt = typeof formatNumber === "function" ? formatNumber : defaultFormatNumber
  var val = Math.max(0, Math.round(Number(kbps) || 0))
  if (val <= 0) return String(unlimitedLabel || "Unlimited")
  if (val >= 1024) {
    var mib = val / 1024
    var decimals = (val % 1024 === 0) ? 0 : 1
    return fmt(mib, decimals) + " MiB/s"
  }
  return fmt(val, 0) + " KiB/s"
}

function sliderStopCount() {
  return SPEED_STOPS_KBPS.length
}

function sliderIndexToKbps(index) {
  var i = Math.round(Number(index) || 0)
  if (i < 0) i = 0
  if (i >= SPEED_STOPS_KBPS.length) i = SPEED_STOPS_KBPS.length - 1
  return SPEED_STOPS_KBPS[i]
}

function kbpsToSliderIndex(kbps) {
  var target = Math.max(SPEED_STOPS_KBPS[0], Number(kbps) || SPEED_STOPS_KBPS[0])
  var bestIdx = 0
  var bestDiff = Math.abs(SPEED_STOPS_KBPS[0] - target)
  for (var i = 1; i < SPEED_STOPS_KBPS.length; i++) {
    var diff = Math.abs(SPEED_STOPS_KBPS[i] - target)
    if (diff < bestDiff) {
      bestDiff = diff
      bestIdx = i
    }
  }
  return bestIdx
}

function emptyHistory() {
  var out = []
  for (var i = 0; i < HISTORY_LENGTH; i++) {
    out.push({ rx: 0, tx: 0 })
  }
  return out
}

function pushSpeedSample(history, rxBytesPerSec, txBytesPerSec) {
  var src = Array.isArray(history) && history.length === HISTORY_LENGTH ? history : emptyHistory()
  var next = src.slice(1)
  next.push({
    rx: Math.max(0, isFinite(rxBytesPerSec) && rxBytesPerSec > 0 ? rxBytesPerSec : 0),
    tx: Math.max(0, isFinite(txBytesPerSec) && txBytesPerSec > 0 ? txBytesPerSec : 0)
  })
  return next
}

function historyPeaks(history) {
  var peakRx = 0
  var peakTx = 0
  if (Array.isArray(history)) {
    for (var i = 0; i < history.length; i++) {
      var pt = history[i]
      if (!pt) continue
      if (pt.rx > peakRx) peakRx = pt.rx
      if (pt.tx > peakTx) peakTx = pt.tx
    }
  }
  return { rx: peakRx, tx: peakTx, max: Math.max(peakRx, peakTx) }
}

function graphScaleCeiling(history) {
  var maxVal = historyPeaks(history).max
  for (var i = 0; i < GRAPH_SCALE_STOPS.length; i++) {
    if (maxVal <= GRAPH_SCALE_STOPS[i] * 0.9) return GRAPH_SCALE_STOPS[i]
  }
  return GRAPH_SCALE_STOPS[GRAPH_SCALE_STOPS.length - 1]
}

function safeGuiUrl(url) {
  var s = String(url || "").trim()
  if (/^https?:\/\/(127\.0\.0\.1|\[::1\])(:\d{1,5})?\/?$/.test(s)) return s
  return "http://127.0.0.1:8384/"
}

function parseSnapshot(rawText) {
  try {
    var obj = JSON.parse(String(rawText || ""))
    if (!obj || typeof obj !== "object") return { ok: false, status: "unavailable" }
    if (obj.actionError) {
      return {
        ok: false,
        status: "error",
        actionError: neutralizeMarkup(obj.actionError, 140)
      }
    }
    var st = String(obj.status || "unavailable")
    if (!VALID_STATUSES[st]) st = "unavailable"
    return {
      ok: obj.ok === true,
      status: st,
      paused: obj.paused === true,
      autoPaused: obj.autoPaused === true,
      selfName: plainText(obj.selfName || "Syncthing", 64) || "Syncthing",
      guiUrl: safeGuiUrl(obj.guiUrl),
      overallCompletion: Math.max(0, Math.min(100, Math.round(Number(obj.overallCompletion) || 0))),
      networks: Array.isArray(obj.networks) ? obj.networks.slice(0, 8) : [],
      transport: (obj.transport && typeof obj.transport === "object") ? obj.transport : {
        mode: "auto",
        activeTypes: [],
        ethernet: { available: false, carrier: false, interfaces: [] },
        wifi: { available: false, carrier: false, interfaces: [] },
        cableWithoutIp: null,
        directLinkLocalInterface: ""
      },
      bandwidth: (obj.bandwidth && typeof obj.bandwidth === "object") ? obj.bandwidth : {
        preset: "unlimited",
        configuredRecvKbps: 0,
        configuredSendKbps: 0,
        effectiveRecvKbps: 0,
        effectiveSendKbps: 0,
        limitWifiOnly: false,
        bypassedOnLan: false
      },
      traffic: (obj.traffic && typeof obj.traffic === "object") ? obj.traffic : null,
      devices: Array.isArray(obj.devices) ? obj.devices.slice(0, 64) : [],
      folders: Array.isArray(obj.folders) ? obj.folders.slice(0, 100) : [],
      hiddenFolders: Math.max(0, Math.round(Number(obj.hiddenFolders) || 0))
    }
  } catch (e) {
    return { ok: false, status: "unavailable" }
  }
}
