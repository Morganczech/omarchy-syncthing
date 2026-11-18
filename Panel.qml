import QtQuick
import QtCore
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "gruut.syncthing"
  ipcTarget: "gruut.syncthing"
  manageIpc: false

  Commons.Translations {
    id: strings
    directory: Qt.resolvedUrl("i18n")
  }

  Settings {
    id: preferences
    category: "gruut.syncthing"
    property bool showBarRates: true
  }

  Service {
    id: service
    settings: root.settings
    panelOpen: root.opened
  }

  readonly property Item panelFlick: panel.loadedContent ? panel.loadedContent.panelFlick : null
  readonly property Item deviceColumn: panel.loadedContent ? panel.loadedContent.deviceColumn : null
  readonly property Item folderColumn: panel.loadedContent ? panel.loadedContent.folderColumn : null

  readonly property color foreground: Commons.Color.popups.text
  readonly property color accent: Commons.Color.accent
  readonly property color urgent: bar ? bar.urgent : Commons.Color.urgent
  readonly property color dim: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.65)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool verticalBar: bar ? bar.vertical : false
  readonly property color hoverFill: Style.hoverFillFor(foreground, accent)
  readonly property color selectedFill: Style.selectedFillFor(foreground, accent)

  readonly property color barBackground: Commons.Color.bar.background
  readonly property bool lightBar: 0.2126 * barBackground.r + 0.7152 * barBackground.g + 0.0722 * barBackground.b > 0.55
  readonly property color barIconColor: {
    if (service.status === "error") return urgent
    if (service.status === "ready") return lightBar ? "#166534" : "#86efac"
    if (service.status === "unavailable" || service.effectivePaused || service.autoPaused) {
      return Qt.darker(barForeground, 1.55)
    }
    return barForeground
  }

  readonly property bool transferring: service.downloadRate > 128 || service.uploadRate > 128
  readonly property bool showBarRates: !verticalBar
    && Boolean(preferences.showBarRates && setting("showBarRates", true))
    && transferring
  readonly property string barRateText: showBarRates
    ? ("\u2193" + Model.formatCompactRate(service.downloadRate, Commons.I18n.formatNumber)
      + " \u2191" + Model.formatCompactRate(service.uploadRate, Commons.I18n.formatNumber))
    : ""

  readonly property var cableWithoutIp: service.transport && service.transport.cableWithoutIp
    ? service.transport.cableWithoutIp
    : null
  readonly property string directLinkLocalIface: service.transport && service.transport.directLinkLocalInterface
    ? String(service.transport.directLinkLocalInterface)
    : ""
  readonly property bool showCableHelper: cableWithoutIp !== null || directLinkLocalIface !== ""
  readonly property bool showCustomSliders: service.effectivePreset === "custom"
  readonly property bool showWifiOnlyToggle: service.effectivePreset !== "unlimited"
  readonly property bool showDevices: service.devices.length > 0
  readonly property bool showFolders: service.folders.length > 0

  readonly property string heroTransportBadge: {
    var mode = service.effectiveTransportMode
    var active = (service.transport && Array.isArray(service.transport.activeTypes))
      ? service.transport.activeTypes
      : []
    if (active.indexOf("ethernet") !== -1 && active.indexOf("wifi") !== -1) return strings.t("badge.both")
    if (active.indexOf("ethernet") !== -1) return strings.t("badge.lan")
    if (active.indexOf("wifi") !== -1) return strings.t("badge.wifi")
    if (mode === "ethernet") return strings.t("badge.lanOnly")
    if (mode === "wifi") return strings.t("badge.wifiOnly")
    return ""
  }

  readonly property string heroMetaText: {
    var base = strings.t("status." + service.status)
    if (service.status === "syncing" && service.overallCompletion < 100) {
      return base + " \u00b7 " + Commons.I18n.formatNumber(service.overallCompletion, 0) + "%"
    }
    return base
  }

  readonly property string barTooltipText: {
    var line = "Syncthing \u00b7 " + strings.t("status." + service.status)
    if (heroTransportBadge !== "") line += " (" + heroTransportBadge + ")"
    if (transferring) {
      line += "  \u2193 " + Model.formatRate(service.downloadRate, Commons.I18n.formatNumber)
        + "  \u2191 " + Model.formatRate(service.uploadRate, Commons.I18n.formatNumber)
    }
    return Model.neutralizeMarkup(line, 160)
  }

  readonly property string activeInterfaceSummary: {
    var t = service.transport || {}
    var parts = []
    var ethList = (t.ethernet && Array.isArray(t.ethernet.interfaces)) ? t.ethernet.interfaces : []
    var wifiList = (t.wifi && Array.isArray(t.wifi.interfaces)) ? t.wifi.interfaces : []
    for (var i = 0; i < ethList.length; i++) {
      if (ethList[i].carrier && ethList[i].ip) {
        parts.push("LAN " + Model.plainText(ethList[i].name, 16) + " \u00b7 " + Model.plainText(ethList[i].ip, 40))
      }
    }
    for (var j = 0; j < wifiList.length; j++) {
      if (wifiList[j].carrier && wifiList[j].ip) {
        parts.push("Wi-Fi " + Model.plainText(wifiList[j].name, 16) + " \u00b7 " + Model.plainText(wifiList[j].ip, 40))
      }
    }
    if (parts.length === 0) return strings.t("network.disconnected")
    return parts.join("  |  ")
  }

  // ---- Unified Keyboard + Pointer Cursor Model ----------------------------

  property bool cursorActive: false
  property bool pointerUnlocked: false
  property string focusSection: "transport"
  property int selectedIndex: 0
  property int folderActionIndex: 0

  PointerMoveGate {
    id: pointerGate
    referenceItem: root.panelFlick
  }

  function focusByPointer(section, idx) {
    if (!pointerUnlocked) return
    cursorActive = true
    focusSection = section
    selectedIndex = idx
  }

  readonly property var transportOptions: [
    {
      value: "auto",
      icon: "\udb80\udc44",
      label: strings.t("transport.auto"),
      tooltip: strings.t("transport.autoHint")
    },
    {
      value: "ethernet",
      icon: "\udb80\ude00",
      label: strings.t("transport.ethernet"),
      tooltip: strings.t("transport.ethernetHint")
    },
    {
      value: "wifi",
      icon: "\udb81\udd69",
      label: strings.t("transport.wifi"),
      tooltip: strings.t("transport.wifiHint")
    }
  ]

  readonly property var bandwidthOptions: [
    { value: "unlimited", label: strings.t("bandwidth.unlimited") },
    { value: "25m", label: "25 MiB/s" },
    { value: "10m", label: "10 MiB/s" },
    { value: "2m", label: "2 MiB/s" },
    { value: "custom", label: strings.t("bandwidth.custom") }
  ]

  readonly property var visibleSections: {
    var list = ["header", "transport"]
    if (showCableHelper) list.push("cable-helper")
    list.push("presets")
    if (showCustomSliders) {
      list.push("recv-slider")
      list.push("send-slider")
    }
    if (showWifiOnlyToggle) list.push("wifi-only")
    if (showDevices) list.push("devices")
    if (showFolders) list.push("folders")
    if (!verticalBar) list.push("bar-rates")
    list.push("open-gui")
    return list
  }

  function sectionCount(section) {
    if (section === "header") return service.available ? 3 : 1
    if (section === "transport") return transportOptions.length
    if (section === "presets") return bandwidthOptions.length
    if (section === "devices") return service.devices.length
    if (section === "folders") return service.folders.length
    return 1
  }

  function sectionIsHorizontal(section) {
    return section === "header" || section === "transport" || section === "presets"
  }

  function sectionIsSlider(section) {
    return section === "recv-slider" || section === "send-slider"
  }

  function ensureCursor() {
    var sections = visibleSections
    if (sections.indexOf(focusSection) === -1) {
      focusSection = sections[0]
      selectedIndex = 0
    }
    var maxIdx = Math.max(0, sectionCount(focusSection) - 1)
    if (selectedIndex < 0) selectedIndex = 0
    if (selectedIndex > maxIdx) selectedIndex = maxIdx
  }

  function moveCursor(dx, dy) {
    cursorActive = true
    ensureCursor()
    if (dy !== 0) {
      var sections = visibleSections
      var sIdx = sections.indexOf(focusSection)
      if (sectionIsHorizontal(focusSection) || sectionIsSlider(focusSection) || sectionCount(focusSection) <= 1) {
        if (dy > 0 && sIdx < sections.length - 1) {
          focusSection = sections[sIdx + 1]
          selectedIndex = 0
        } else if (dy < 0 && sIdx > 0) {
          focusSection = sections[sIdx - 1]
          selectedIndex = sectionIsHorizontal(focusSection) ? 0 : Math.max(0, sectionCount(focusSection) - 1)
        }
      } else {
        var nextRow = selectedIndex + dy
        if (nextRow < 0) {
          if (sIdx > 0) {
            focusSection = sections[sIdx - 1]
            selectedIndex = sectionIsHorizontal(focusSection) ? 0 : Math.max(0, sectionCount(focusSection) - 1)
          }
        } else if (nextRow >= sectionCount(focusSection)) {
          if (sIdx < sections.length - 1) {
            focusSection = sections[sIdx + 1]
            selectedIndex = 0
          }
        } else {
          selectedIndex = nextRow
        }
      }
      folderActionIndex = 0
      ensureCursor()
      scrollKeyboardCursorIntoView()
      return
    }

    if (dx !== 0) {
      if (sectionIsSlider(focusSection)) {
        adjustSliderByStep(focusSection, dx)
        return
      }
      if (focusSection === "folders") {
        folderActionIndex = Math.max(0, Math.min(2, folderActionIndex + dx))
        return
      }
      if (sectionIsHorizontal(focusSection)) {
        var maxH = Math.max(0, sectionCount(focusSection) - 1)
        selectedIndex = Math.max(0, Math.min(maxH, selectedIndex + dx))
      }
    }
  }

  function adjustSliderByStep(section, delta) {
    if (section === "recv-slider") {
      var curR = Model.kbpsToSliderIndex(service.effectiveConfiguredRecvKbps)
      var nextR = Model.sliderIndexToKbps(curR + delta)
      service.setCustomBandwidth(nextR, service.effectiveConfiguredSendKbps)
    } else if (section === "send-slider") {
      var curS = Model.kbpsToSliderIndex(service.effectiveConfiguredSendKbps)
      var nextS = Model.sliderIndexToKbps(curS + delta)
      service.setCustomBandwidth(service.effectiveConfiguredRecvKbps, nextS)
    }
  }

  function openWebGui() {
    root.close()
    Qt.openUrlExternally(Model.safeGuiUrl(service.guiUrl))
  }

  function activateCursor() {
    ensureCursor()
    if (focusSection === "header") {
      if (!service.available) {
        openWebGui()
        return
      }
      if (selectedIndex === 0) service.rescanAll()
      else if (selectedIndex === 1) openWebGui()
      else service.toggleGlobalPause()
      return
    }
    if (focusSection === "transport") {
      if (selectedIndex >= 0 && selectedIndex < transportOptions.length) {
        service.setTransportMode(transportOptions[selectedIndex].value)
      }
      return
    }
    if (focusSection === "cable-helper") {
      if (cableWithoutIp && cableWithoutIp.interface) {
        service.enableDirectCable(cableWithoutIp.interface)
      } else if (directLinkLocalIface !== "") {
        service.restoreDhcpCable(directLinkLocalIface)
      }
      return
    }
    if (focusSection === "presets") {
      if (selectedIndex >= 0 && selectedIndex < bandwidthOptions.length) {
        service.setBandwidthPreset(bandwidthOptions[selectedIndex].value)
      }
      return
    }
    if (focusSection === "wifi-only") {
      service.toggleLimitWifiOnly()
      return
    }
    if (focusSection === "devices") {
      var dev = service.devices[selectedIndex]
      if (dev && dev.id) service.setDevicePaused(dev.id, !dev.paused)
      return
    }
    if (focusSection === "folders") {
      var f = service.folders[selectedIndex]
      if (!f || !f.id) return
      if (folderActionIndex === 0 && f.canOpen) {
        service.openFolder(f.id)
        root.close()
      } else if (folderActionIndex === 1 && !f.paused) {
        service.rescanFolder(f.id)
      } else {
        service.setFolderPaused(f.id, !f.paused)
      }
      return
    }
    if (focusSection === "bar-rates") {
      preferences.showBarRates = !preferences.showBarRates
      return
    }
    if (focusSection === "open-gui") {
      openWebGui()
    }
  }

  function scrollItemIntoView(item) {
    if (!panelFlick || !item) return
    Qt.callLater(function() {
      if (!item || !panelFlick) return
      var margin = Style.space(8)
      var pt = item.mapToItem(panelFlick.contentItem, 0, 0)
      var top = pt.y
      var bottom = top + item.height
      var viewTop = panelFlick.contentY
      var viewBottom = viewTop + panelFlick.height
      var maxY = Math.max(0, panelFlick.contentHeight - panelFlick.height)
      if (top < viewTop + margin) panelFlick.contentY = Math.max(0, top - margin)
      else if (bottom > viewBottom - margin) panelFlick.contentY = Math.min(maxY, bottom + margin - panelFlick.height)
    })
  }

  function scrollKeyboardCursorIntoView() {
    var content = panel.loadedContent
    if (!content) return
    if (focusSection === "header") scrollItemIntoView(content.heroSection)
    else if (focusSection === "transport") scrollItemIntoView(content.transportSection)
    else if (focusSection === "cable-helper") scrollItemIntoView(content.cableHelperCard)
    else if (focusSection === "presets") scrollItemIntoView(content.presetsRowHolder)
    else if (focusSection === "recv-slider") scrollItemIntoView(content.recvSliderRow)
    else if (focusSection === "send-slider") scrollItemIntoView(content.sendSliderRow)
    else if (focusSection === "wifi-only") scrollItemIntoView(content.wifiOnlyToggle)
    else if (focusSection === "devices" && deviceColumn && selectedIndex < deviceColumn.children.length) {
      scrollItemIntoView(deviceColumn.children[selectedIndex])
    } else if (focusSection === "folders" && folderColumn && selectedIndex < folderColumn.children.length) {
      scrollItemIntoView(folderColumn.children[selectedIndex])
    } else if (focusSection === "bar-rates") scrollItemIntoView(content.barRatesToggle)
    else if (focusSection === "open-gui") scrollItemIntoView(content.openGuiButton)
  }

  onVisibleSectionsChanged: ensureCursor()
  onOpenedChanged: {
    if (opened) {
      cursorActive = false
      pointerUnlocked = false
      pointerGate.reset()
      if (panelFlick) panelFlick.contentY = 0
      service.refresh()
      Qt.callLater(function() {
        if (panelFlick) panelFlick.contentY = 0
        if (panel.loadedContent) panel.loadedContent.forceActiveFocus()
      })
    }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  ShellIpc {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): string { service.refresh(); return "ok" }
    function pause(): string { service.pauseAll(); return "ok" }
    function resume(): string { service.resumeAll(); return "ok" }
    function togglePause(): string { service.toggleGlobalPause(); return "ok" }
    function rescan(): string { service.rescanAll(); return "ok" }
    function setTransport(mode: string): string {
      if (mode !== "auto" && mode !== "ethernet" && mode !== "wifi") return "invalid mode"
      service.setTransportMode(mode)
      return "ok"
    }
    function status(): string { return service.status }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    labelText: root.barRateText
    slotSize: root.barRateText !== ""
      ? (Style.bar.iconCanvas + barLabelMetrics.advanceWidth(root.barRateText) + Style.space(22))
      : Style.bar.iconSlot
    tooltipText: root.barTooltipText

    iconComponent: Component {
      Item {
        SyncthingIcon {
          x: (parent.width - width) / 2
          y: (parent.height - height) / 2
          iconSize: button.iconSize
          color: root.barIconColor
          badgeColor: root.urgent
          syncing: service.status === "syncing" && !(root.bar && root.bar.barHidden)
          warning: service.status === "error"
          paused: service.effectivePaused || service.autoPaused
        }
      }
    }

    onPressed: function(buttonCode) {
      if (buttonCode === Qt.RightButton) service.toggleGlobalPause()
      else if (buttonCode === Qt.MiddleButton) service.rescanAll()
      else root.toggle()
    }
  }

  FontMetrics {
    id: barLabelMetrics
    font.family: root.fontFamily
    font.pixelSize: Style.bar.iconFont
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(416))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(740))

    content: PanelKeyCatcher {
      id: keyCatcher
      property alias panelFlick: panelFlick
      property alias heroSection: heroSection
      property alias transportSection: transportSection
      property alias cableHelperCard: cableHelperCard
      property alias presetsRowHolder: presetsRowHolder
      property alias recvSliderRow: recvSliderRow
      property alias sendSliderRow: sendSliderRow
      property alias wifiOnlyToggle: wifiOnlyToggle
      property alias deviceColumn: deviceColumn
      property alias folderColumn: folderColumn
      property alias barRatesToggle: barRatesToggle
      property alias openGuiButton: openGuiButton

      implicitHeight: mainColumn.implicitHeight
      anchors.fill: parent

      HoverHandler {
        onPointChanged: {
          if (!root.pointerUnlocked && pointerGate.moved(keyCatcher, point.position)) {
            root.pointerUnlocked = true
          }
        }
      }

      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) {
          root.cursorActive = true
          root.ensureCursor()
          return
        }
        root.moveCursor(dx, dy)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "p" || t === "P") service.toggleGlobalPause()
        else if (t === "r" || t === "R") service.rescanAll()
        else if (t === "o" || t === "O") root.openWebGui()
        else if (t === "1") service.setTransportMode("auto")
        else if (t === "2") service.setTransportMode("ethernet")
        else if (t === "3") service.setTransportMode("wifi")
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: mainColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: mainColumn
          width: panelFlick.width
          spacing: Style.space(12)

          // ---- Hero Header -------------------------------------------------
          Item {
            id: heroSection
            width: parent.width
            implicitHeight: hero.implicitHeight
            readonly property bool sectionFocused: root.cursorActive && root.focusSection === "header"
            readonly property int headerCursorIdx: sectionFocused ? root.selectedIndex : -1
            readonly property bool availableNow: service.available
            readonly property bool pausedNow: service.effectivePaused
            readonly property bool busyNow: service.busy
            readonly property string rescanTip: strings.t("actions.rescanAll")
            readonly property string guiTip: strings.t("settings.open")
            readonly property string pauseTip: pausedNow ? strings.t("actions.resumeSync") : strings.t("actions.pauseSync")

            onSectionFocusedChanged: {}

            function focusHeaderItem(idx) {
              root.focusByPointer("header", idx)
            }

            PanelHero {
              id: hero
              width: parent.width
              title: Model.neutralizeMarkup(service.selfName, 48)
              detail: Model.neutralizeMarkup(root.heroTransportBadge, 24)
              meta: Model.neutralizeMarkup(root.heroMetaText, 72)
              foreground: root.foreground
              fontFamily: root.fontFamily
              iconOpacity: service.active ? 1.0 : 0.55

              iconComponent: Component {
                SyncthingIcon {
                  iconSize: Style.font.display
                  color: service.status === "error" ? root.urgent : (service.active ? root.accent : root.dim)
                  badgeColor: root.urgent
                  syncing: service.status === "syncing"
                  warning: service.status === "error"
                  paused: service.effectivePaused || service.autoPaused
                }
              }

              trailingControl: Component {
                Row {
                  spacing: Style.space(8)

                  PanelActionButton {
                    visible: heroSection.availableNow
                    iconText: "\udb81\udc50"
                    tooltipText: heroSection.rescanTip
                    foreground: hero.foreground
                    fontFamily: hero.fontFamily
                    size: Style.space(26)
                    enabled: !heroSection.busyNow
                    hasCursor: heroSection.headerCursorIdx === 0
                    onHovered: function(on) { if (on) heroSection.focusHeaderItem(0) }
                    onClicked: service.rescanAll()
                  }

                  PanelActionButton {
                    iconText: "\udb81\udd9f"
                    tooltipText: heroSection.guiTip
                    foreground: hero.foreground
                    fontFamily: hero.fontFamily
                    size: Style.space(26)
                    hasCursor: heroSection.availableNow
                      ? (heroSection.headerCursorIdx === 1)
                      : (heroSection.headerCursorIdx === 0)
                    onHovered: function(on) {
                      if (on) heroSection.focusHeaderItem(heroSection.availableNow ? 1 : 0)
                    }
                    onClicked: root.openWebGui()
                  }

                  ToggleSwitch {
                    id: globalSwitch
                    visible: heroSection.availableNow
                    checked: !heroSection.pausedNow
                    busy: heroSection.busyNow
                    foreground: hero.foreground
                    accent: root.accent
                    anchors.verticalCenter: parent.verticalCenter
                    hasCursor: heroSection.headerCursorIdx === 2
                    onHovered: function(on) { if (on) heroSection.focusHeaderItem(2) }
                    onToggled: service.toggleGlobalPause()

                    PanelToolTip {
                      visible: globalSwitch.containsMouse
                      text: heroSection.pauseTip
                      fontFamily: hero.fontFamily
                    }
                  }
                }
              }
            }
          }

          // ---- Diagnostic / Unavailable Banner -----------------------------
          BorderSurface {
            visible: service.lastError !== "" || !service.available
            width: parent.width
            implicitHeight: diagText.implicitHeight + Style.space(18)
            radius: Style.cornerRadius
            color: service.lastError !== ""
              ? Qt.rgba(root.urgent.r, root.urgent.g, root.urgent.b, 0.10)
              : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.04)
            borderSpec: Border.flat(
              service.lastError !== ""
                ? Qt.rgba(root.urgent.r, root.urgent.g, root.urgent.b, 0.35)
                : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12),
              1
            )

            Text {
              id: diagText
              textFormat: Text.PlainText
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.margins: Style.space(10)
              text: service.lastError !== "" ? service.lastError : strings.t("status.unavailableHelp")
              color: service.lastError !== "" ? root.urgent : root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
          }

          PanelSeparator { foreground: root.foreground }

          // ---- Network Transport Mode --------------------------------------
          Column {
            id: transportSection
            width: parent.width
            spacing: Style.space(8)
            readonly property bool sectionFocused: root.cursorActive && root.focusSection === "transport"

            RowLayout {
              width: parent.width
              spacing: Style.space(8)

              PanelSectionHeader {
                text: strings.t("transport.header")
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              Item { Layout.fillWidth: true; height: 1 }

              Text {
                textFormat: Text.PlainText
                text: root.activeInterfaceSummary
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
                Layout.maximumWidth: Style.space(220)
              }
            }

            ButtonGroup {
              id: transportGroup
              options: root.transportOptions
              value: service.effectiveTransportMode
              foreground: root.foreground
              accent: root.accent
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              focusable: false
              cursorIndex: transportSection.sectionFocused ? root.selectedIndex : -1
              onChanged: function(mode) {
                root.cursorActive = true
                root.focusSection = "transport"
                service.setTransportMode(mode)
              }
              onHovered: function(idx, isHovered) {
                if (isHovered) root.focusByPointer("transport", idx)
              }
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: {
                if (service.effectiveTransportMode === "ethernet") return strings.t("transport.ethernetDesc")
                if (service.effectiveTransportMode === "wifi") return strings.t("transport.wifiDesc")
                return strings.t("transport.autoDesc")
              }
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            // Direct PC <-> Notebook Cable Link-Local Helper
            CursorSurface {
              id: cableHelperCard
              visible: root.showCableHelper
              width: parent.width
              implicitHeight: cableRow.implicitHeight + Style.space(18)
              foreground: root.foreground
              fill: root.hoverFill
              hasCursor: root.cursorActive && root.focusSection === "cable-helper"

              readonly property bool needsLinkLocal: root.cableWithoutIp !== null
              readonly property string targetIface: needsLinkLocal
                ? Model.plainText(root.cableWithoutIp.interface, 16)
                : Model.plainText(root.directLinkLocalIface, 16)

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                enabled: !service.busy && (!cableHelperCard.needsLinkLocal || Boolean(root.cableWithoutIp && root.cableWithoutIp.nmAvailable))
                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                onEntered: root.focusByPointer("cable-helper", 0)
                onClicked: {
                  if (cableHelperCard.needsLinkLocal) service.enableDirectCable(cableHelperCard.targetIface)
                  else service.restoreDhcpCable(cableHelperCard.targetIface)
                }
              }

              RowLayout {
                id: cableRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.space(10)
                anchors.rightMargin: Style.space(10)
                spacing: Style.space(10)

                Text {
                  textFormat: Text.PlainText
                  text: "\udb80\ude00"
                  color: cableHelperCard.needsLinkLocal ? root.accent : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.heading
                  Layout.alignment: Qt.AlignVCenter
                }

                ColumnLayout {
                  Layout.fillWidth: true
                  spacing: Style.space(2)

                  Text {
                    textFormat: Text.PlainText
                    Layout.fillWidth: true
                    text: cableHelperCard.needsLinkLocal
                      ? strings.t("cable.detectedTitle").arg(cableHelperCard.targetIface)
                      : strings.t("cable.activeTitle").arg(cableHelperCard.targetIface)
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                    elide: Text.ElideRight
                  }

                  Text {
                    textFormat: Text.PlainText
                    Layout.fillWidth: true
                    text: cableHelperCard.needsLinkLocal
                      ? strings.t("cable.detectedHelp")
                      : strings.t("cable.activeHelp")
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.caption
                    wrapMode: Text.WordWrap
                  }
                }

                Button {
                  text: cableHelperCard.needsLinkLocal
                    ? strings.t("cable.enableAction")
                    : strings.t("cable.restoreDhcpAction")
                  bordered: true
                  focusable: false
                  foreground: root.foreground
                  accent: root.accent
                  fontFamily: root.fontFamily
                  fontSize: Style.font.caption
                  enabled: !service.busy && (!cableHelperCard.needsLinkLocal || Boolean(root.cableWithoutIp && root.cableWithoutIp.nmAvailable))
                  onClicked: {
                    if (cableHelperCard.needsLinkLocal) service.enableDirectCable(cableHelperCard.targetIface)
                    else service.restoreDhcpCable(cableHelperCard.targetIface)
                  }
                }
              }
            }
          }

          PanelSeparator { foreground: root.foreground }

          // ---- Live Speed Graph & Bandwidth Controls -----------------------
          Column {
            id: transferSection
            width: parent.width
            spacing: Style.space(10)

            SpeedGraph {
              width: parent.width
              history: service.speedHistory
              downloadRate: service.downloadRate
              uploadRate: service.uploadRate
              foreground: root.foreground
              accent: root.accent
              fontFamily: root.fontFamily
              downloadLabel: strings.t("graph.download")
              uploadLabel: strings.t("graph.upload")
              peakLabel: strings.t("graph.peak")
              scaleLabel: strings.t("graph.scale")
            }

            RowLayout {
              width: parent.width
              spacing: Style.space(8)

              PanelSectionHeader {
                text: strings.t("bandwidth.header")
                foreground: root.foreground
                fontFamily: root.fontFamily
              }

              Item { Layout.fillWidth: true; height: 1 }

              Text {
                textFormat: Text.PlainText
                text: {
                  if (service.bandwidth && service.bandwidth.bypassedOnLan) {
                    return strings.t("bandwidth.bypassedOnLan")
                  }
                  var r = service.effectiveConfiguredRecvKbps
                  var s = service.effectiveConfiguredSendKbps
                  if (r <= 0 && s <= 0) return strings.t("bandwidth.unlimited")
                  var u = strings.t("bandwidth.unlimited")
                  return "\u2193 " + Model.formatKbpsLimit(r, Commons.I18n.formatNumber, u)
                    + "  \u2191 " + Model.formatKbpsLimit(s, Commons.I18n.formatNumber, u)
                }
                color: (service.bandwidth && service.bandwidth.bypassedOnLan) ? root.accent : root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
              }
            }

            Item {
              id: presetsRowHolder
              width: parent.width
              implicitHeight: bandwidthGroup.implicitHeight
              readonly property bool sectionFocused: root.cursorActive && root.focusSection === "presets"

              ButtonGroup {
                id: bandwidthGroup
                options: root.bandwidthOptions
                value: service.effectivePreset
                foreground: root.foreground
                accent: root.accent
                fontFamily: root.fontFamily
                fontSize: Style.font.caption
                focusable: false
                cursorIndex: presetsRowHolder.sectionFocused ? root.selectedIndex : -1
                onChanged: function(preset) {
                  root.cursorActive = true
                  root.focusSection = "presets"
                  service.setBandwidthPreset(preset)
                }
                onHovered: function(idx, isHovered) {
                  if (isHovered) root.focusByPointer("presets", idx)
                }
              }
            }

            // Custom Download / Upload Notched Sliders
            Column {
              visible: root.showCustomSliders
              width: parent.width
              spacing: Style.space(6)

              SpeedLimitSliderRow {
                id: recvSliderRow
                width: parent.width
                directionGlyph: "\u2193"
                kbpsValue: service.effectiveConfiguredRecvKbps
                hasCursor: root.cursorActive && root.focusSection === "recv-slider"
                onPointerEntered: root.focusByPointer("recv-slider", 0)
                onCommittedKbps: function(kbps) {
                  service.setCustomBandwidth(kbps, service.effectiveConfiguredSendKbps)
                }
              }

              SpeedLimitSliderRow {
                id: sendSliderRow
                width: parent.width
                directionGlyph: "\u2191"
                kbpsValue: service.effectiveConfiguredSendKbps
                hasCursor: root.cursorActive && root.focusSection === "send-slider"
                onPointerEntered: root.focusByPointer("send-slider", 0)
                onCommittedKbps: function(kbps) {
                  service.setCustomBandwidth(service.effectiveConfiguredRecvKbps, kbps)
                }
              }
            }

            Toggle {
              id: wifiOnlyToggle
              visible: root.showWifiOnlyToggle
              width: parent.width
              label: strings.t("bandwidth.wifiOnlyLabel")
              description: strings.t("bandwidth.wifiOnlyDesc")
              foreground: root.foreground
              accent: root.accent
              fontFamily: root.fontFamily
              titleSize: Style.font.body
              checked: service.effectiveLimitWifiOnly
              hasCursor: root.cursorActive && root.focusSection === "wifi-only"
              onHovered: function(on) {
                if (on) root.focusByPointer("wifi-only", 0)
              }
              onClicked: {
                root.cursorActive = true
                root.focusSection = "wifi-only"
                service.toggleLimitWifiOnly()
              }
            }
          }

          // ---- Connected / Configured Devices ------------------------------
          PanelSeparator {
            visible: root.showDevices
            foreground: root.foreground
          }

          Column {
            visible: root.showDevices
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: strings.t("devices.header").arg(Commons.I18n.formatNumber(service.devices.length, 0))
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Column {
              id: deviceColumn
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: service.devices
                DeviceRow {
                  required property var modelData
                  required property int index
                  width: deviceColumn.width
                  device: modelData
                  rowIndex: index
                }
              }
            }
          }

          // ---- Shared Folders ----------------------------------------------
          PanelSeparator {
            visible: root.showFolders
            foreground: root.foreground
          }

          Column {
            visible: root.showFolders
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: strings.t("folders.header").arg(Commons.I18n.formatNumber(service.folders.length, 0))
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Column {
              id: folderColumn
              width: parent.width
              spacing: Style.space(6)

              Repeater {
                model: service.folders
                FolderRow {
                  required property var modelData
                  required property int index
                  width: folderColumn.width
                  folder: modelData
                  rowIndex: index
                }
              }
            }

            Text {
              textFormat: Text.PlainText
              visible: service.hiddenFolders > 0
              text: strings.t("folders.more").arg(Commons.I18n.formatNumber(service.hiddenFolders, 0))
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          PanelSeparator { foreground: root.foreground }

          // ---- Footer Preferences & Web GUI --------------------------------
          Column {
            width: parent.width
            spacing: Style.space(8)

            Toggle {
              id: barRatesToggle
              visible: !root.verticalBar
              width: parent.width
              label: strings.t("settings.barRates")
              description: strings.t("settings.barRatesDesc")
              foreground: root.foreground
              accent: root.accent
              fontFamily: root.fontFamily
              titleSize: Style.font.body
              checked: preferences.showBarRates
              hasCursor: root.cursorActive && root.focusSection === "bar-rates"
              onHovered: function(on) {
                if (on) root.focusByPointer("bar-rates", 0)
              }
              onClicked: {
                root.cursorActive = true
                root.focusSection = "bar-rates"
                preferences.showBarRates = !preferences.showBarRates
              }
            }

            Button {
              id: openGuiButton
              width: parent.width
              iconText: "\udb81\udd9f"
              text: strings.t("settings.open")
              bordered: true
              focusable: false
              foreground: root.foreground
              accent: root.accent
              fontFamily: root.fontFamily
              hasCursor: root.cursorActive && root.focusSection === "open-gui"
              onHovered: function(on) {
                if (on) root.focusByPointer("open-gui", 0)
              }
              onClicked: root.openWebGui()
            }
          }
        }
      }
    }
  }

  // ---- Reusable Subcomponents ----------------------------------------------

  component SpeedLimitSliderRow: CursorSurface {
    id: sliderCard

    property string directionGlyph: "\u2193"
    property int kbpsValue: 10240
    signal pointerEntered()
    signal committedKbps(int kbps)

    readonly property int currentStopIdx: Model.kbpsToSliderIndex(kbpsValue)
    readonly property int previewStopIdx: slider.dragging ? Math.round(slider.liveValue) : currentStopIdx
    readonly property int shownKbps: Model.sliderIndexToKbps(previewStopIdx)

    outline: true
    foreground: root.foreground
    implicitHeight: innerSliderRow.implicitHeight + Style.space(14)

    HoverHandler {
      onHoveredChanged: if (hovered) sliderCard.pointerEntered()
    }

    RowLayout {
      id: innerSliderRow
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(10)
      spacing: Style.space(10)

      Text {
        textFormat: Text.PlainText
        text: sliderCard.directionGlyph
        color: sliderCard.directionGlyph === "\u2193" ? root.accent : root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
        Layout.preferredWidth: Style.space(16)
        horizontalAlignment: Text.AlignHCenter
      }

      PanelSlider {
        id: slider
        bar: root.bar
        Layout.fillWidth: true
        minimum: 0
        maximum: Model.sliderStopCount() - 1
        step: 1
        integer: true
        tickCount: Model.sliderStopCount()
        value: sliderCard.currentStopIdx
        fillColor: sliderCard.directionGlyph === "\u2193" ? root.accent : root.foreground
        onReleased: function(v) {
          sliderCard.committedKbps(Model.sliderIndexToKbps(v))
        }
      }

      Text {
        textFormat: Text.PlainText
        text: Model.formatKbpsLimit(sliderCard.shownKbps, Commons.I18n.formatNumber, strings.t("bandwidth.unlimited"))
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
        Layout.preferredWidth: Style.space(76)
        horizontalAlignment: Text.AlignRight
      }
    }
  }

  component DeviceRow: CursorSurface {
    id: devRow

    property var device: ({})
    property int rowIndex: 0

    readonly property bool isConnected: Boolean(device && device.connected)
    readonly property bool isPaused: Boolean(device && device.paused)
    readonly property string netKind: device ? String(device.network || "disconnected") : "disconnected"
    readonly property string devName: Model.plainText(device ? device.name : "", 64)
    readonly property string devAddr: Model.plainText(device ? device.address : "", 48)
    readonly property int completion: device ? Number(device.completion || 100) : 100
    readonly property double needBytes: device ? Number(device.needBytes || 0) : 0

    hasCursor: root.cursorActive && root.focusSection === "devices" && root.selectedIndex === rowIndex
    current: isConnected
    foreground: root.foreground
    fill: root.hoverFill
    currentFill: root.selectedFill
    implicitHeight: devLayout.implicitHeight + Style.space(16)

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      onPositionChanged: root.focusByPointer("devices", devRow.rowIndex)
    }

    RowLayout {
      id: devLayout
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(10)

      Text {
        textFormat: Text.PlainText
        text: {
          if (devRow.isPaused) return "\udb80\udfe4"
          if (devRow.netKind === "ethernet") return "\udb80\ude00"
          if (devRow.netKind === "wifi") return "\udb81\udd69"
          return "\udb80\udf19"
        }
        color: devRow.isConnected
          ? (devRow.netKind === "ethernet" ? root.accent : root.foreground)
          : root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.icon
        Layout.alignment: Qt.AlignVCenter
      }

      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(2)

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: devRow.devName
          color: devRow.isPaused ? root.dim : root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: devRow.isConnected
          elide: Text.ElideRight
        }

        Text {
          textFormat: Text.PlainText
          Layout.fillWidth: true
          text: {
            if (devRow.isPaused) return strings.t("status.paused")
            if (!devRow.isConnected) return strings.t("network.disconnected")
            var parts = [strings.t("network." + devRow.netKind)]
            if (devRow.devAddr !== "") parts.push(devRow.devAddr)
            if (devRow.completion < 100) {
              parts.push(Commons.I18n.formatNumber(devRow.completion, 0) + "%")
              if (devRow.needBytes > 0) {
                parts.push(Model.formatBytes(devRow.needBytes, Commons.I18n.formatNumber))
              }
            } else {
              parts.push(strings.t("status.ready"))
            }
            return parts.join(" \u00b7 ")
          }
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      PanelActionButton {
        iconText: devRow.isPaused ? "\udb81\udc0a" : "\udb80\udfe4"
        tooltipText: Model.neutralizeMarkup(
          devRow.isPaused ? strings.t("actions.resumeDevice") : strings.t("actions.pauseDevice"),
          80
        )
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !service.busy && Boolean(devRow.device && devRow.device.id)
        hasCursor: devRow.hasCursor
        onClicked: if (devRow.device && devRow.device.id) {
          service.setDevicePaused(devRow.device.id, !devRow.isPaused)
        }
      }
    }
  }

  component FolderRow: CursorSurface {
    id: fRow

    property var folder: ({})
    property int rowIndex: 0

    readonly property string folderId: folder ? String(folder.id || "") : ""
    readonly property string folderName: Model.plainText(folder ? folder.name : "", 72)
    readonly property string folderState: folder ? String(folder.state || "ready") : "ready"
    readonly property bool isPaused: Boolean(folder && folder.paused)
    readonly property bool canOpen: Boolean(folder && folder.canOpen)
    readonly property int completion: folder ? Number(folder.completion || 100) : 100
    readonly property double needBytes: folder ? Number(folder.needBytes || 0) : 0
    readonly property bool showProgress: (folderState === "syncing" || folderState === "pending") && completion < 100

    hasCursor: root.cursorActive && root.focusSection === "folders" && root.selectedIndex === rowIndex
    foreground: root.foreground
    fill: root.hoverFill
    implicitHeight: folderCol.implicitHeight + Style.space(16)

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      onPositionChanged: root.focusByPointer("folders", fRow.rowIndex)
    }

    Column {
      id: folderCol
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(8)
      spacing: Style.space(6)

      RowLayout {
        width: parent.width
        spacing: Style.space(8)

        Text {
          textFormat: Text.PlainText
          text: {
            if (fRow.folderState === "error") return "\udb80\udc26"
            if (fRow.folderState === "syncing") return "\udb81\udc50"
            if (fRow.folderState === "paused") return "\udb80\udfe4"
            if (fRow.folderState === "ready") return "\udb80\udd2c"
            return "\udb80\uded4"
          }
          color: {
            if (fRow.folderState === "error") return root.urgent
            if (fRow.folderState === "syncing") return root.accent
            if (fRow.folderState === "paused" || fRow.folderState === "offline") return root.dim
            return root.foreground
          }
          font.family: root.fontFamily
          font.pixelSize: Style.font.icon
          Layout.alignment: Qt.AlignVCenter
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.space(1)

          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            text: fRow.folderName
            color: fRow.isPaused ? root.dim : root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
          }

          Text {
            textFormat: Text.PlainText
            Layout.fillWidth: true
            text: {
              var st = strings.t("status." + fRow.folderState)
              if (fRow.showProgress) {
                st += " \u00b7 " + Commons.I18n.formatNumber(fRow.completion, 0) + "%"
                if (fRow.needBytes > 0) {
                  st += " (" + Model.formatBytes(fRow.needBytes, Commons.I18n.formatNumber) + ")"
                }
              }
              return st
            }
            color: fRow.folderState === "error" ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
          }
        }

        PanelActionButton {
          visible: fRow.canOpen
          iconText: "\udb81\udf70"
          tooltipText: strings.t("actions.openFolder")
          foreground: root.foreground
          fontFamily: root.fontFamily
          hasCursor: fRow.hasCursor && root.folderActionIndex === 0
          onClicked: {
            service.openFolder(fRow.folderId)
            root.close()
          }
        }

        PanelActionButton {
          iconText: "\udb81\udc50"
          tooltipText: strings.t("actions.rescanFolder")
          foreground: root.foreground
          fontFamily: root.fontFamily
          enabled: !service.busy && !fRow.isPaused && fRow.folderId !== ""
          hasCursor: fRow.hasCursor && root.folderActionIndex === 1
          onClicked: service.rescanFolder(fRow.folderId)
        }

        PanelActionButton {
          iconText: fRow.isPaused ? "\udb81\udc0a" : "\udb80\udfe4"
          tooltipText: fRow.isPaused ? strings.t("actions.resumeFolder") : strings.t("actions.pauseFolder")
          foreground: root.foreground
          fontFamily: root.fontFamily
          enabled: !service.busy && fRow.folderId !== ""
          hasCursor: fRow.hasCursor && root.folderActionIndex === 2
          onClicked: service.setFolderPaused(fRow.folderId, !fRow.isPaused)
        }
      }

      // Smooth progress bar during active or pending sync.
      Rectangle {
        visible: fRow.showProgress
        width: parent.width
        height: Style.space(3)
        radius: height / 2
        color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)

        Rectangle {
          width: Math.max(parent.height, parent.width * (fRow.completion / 100))
          height: parent.height
          radius: parent.radius
          color: root.accent

          Behavior on width {
            NumberAnimation { duration: Style.duration(220); easing.type: Easing.OutCubic }
          }
        }
      }
    }
  }
}
