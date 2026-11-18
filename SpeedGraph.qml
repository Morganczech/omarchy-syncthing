import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

BorderSurface {
  id: root

  property var history: []
  property real downloadRate: -1
  property real uploadRate: -1
  property color foreground: Commons.Color.foreground
  property color accent: Commons.Color.accent
  property string fontFamily: Style.font.family
  property string downloadLabel: "Download"
  property string uploadLabel: "Upload"
  property string peakLabel: "Peak"
  property string scaleLabel: "Scale"

  readonly property color dim: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.65)
  readonly property color uploadStroke: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.72)
  readonly property var peaks: Model.historyPeaks(history)
  readonly property real targetCeiling: Model.graphScaleCeiling(history)
  property real animatedCeiling: targetCeiling

  onTargetCeilingChanged: animatedCeiling = targetCeiling

  Behavior on animatedCeiling {
    NumberAnimation {
      duration: Style.duration(260)
      easing.type: Easing.OutCubic
    }
  }

  width: parent ? parent.width : Style.space(350)
  implicitHeight: innerCol.implicitHeight + Style.space(22)
  radius: Style.cornerRadius
  color: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.04)
  borderSpec: Border.flat(Qt.rgba(foreground.r, foreground.g, foreground.b, 0.10), 1)

  function buildLinePath(seriesKey, w, h, closeArea) {
    var pts = root.history
    if (!Array.isArray(pts) || pts.length < 2 || w <= 2 || h <= 2) return ""
    var ceil = Math.max(1024, root.animatedCeiling)
    var count = pts.length
    var stepX = w / (count - 1)
    var coords = []
    for (var i = 0; i < count; i++) {
      var raw = pts[i] ? Number(pts[i][seriesKey] || 0) : 0
      var clamped = Math.max(0, Math.min(1, raw / ceil))
      var px = i * stepX
      var py = h - clamped * (h - 3) - 1.5
      coords.push({ x: px, y: py })
    }

    var d = "M " + coords[0].x.toFixed(1) + " " + coords[0].y.toFixed(1)
    for (var j = 0; j < coords.length - 1; j++) {
      var p0 = coords[j]
      var p1 = coords[j + 1]
      var midX = ((p0.x + p1.x) * 0.5).toFixed(1)
      d += " C " + midX + " " + p0.y.toFixed(1)
        + ", " + midX + " " + p1.y.toFixed(1)
        + ", " + p1.x.toFixed(1) + " " + p1.y.toFixed(1)
    }

    if (closeArea) {
      d += " L " + w.toFixed(1) + " " + h.toFixed(1)
        + " L 0 " + h.toFixed(1) + " Z"
    }
    return d
  }

  Column {
    id: innerCol
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    anchors.leftMargin: Style.space(12)
    anchors.rightMargin: Style.space(12)
    spacing: Style.space(8)

    // Live rate readouts + peak/scale caption.
    RowLayout {
      width: parent.width
      spacing: Style.space(12)

      Column {
        spacing: Style.space(1)

        Row {
          spacing: Style.space(5)
          Rectangle {
            width: Style.space(7)
            height: Style.space(7)
            radius: width / 2
            color: root.accent
            anchors.verticalCenter: parent.verticalCenter
          }
          Text {
            textFormat: Text.PlainText
            text: root.downloadLabel.toUpperCase()
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 0.8
          }
        }

        Text {
          textFormat: Text.PlainText
          text: "\u2193 " + Model.formatRate(root.downloadRate, Commons.I18n.formatNumber)
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }
      }

      Column {
        spacing: Style.space(1)

        Row {
          spacing: Style.space(5)
          Rectangle {
            width: Style.space(7)
            height: Style.space(7)
            radius: width / 2
            color: root.uploadStroke
            anchors.verticalCenter: parent.verticalCenter
          }
          Text {
            textFormat: Text.PlainText
            text: root.uploadLabel.toUpperCase()
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 0.8
          }
        }

        Text {
          textFormat: Text.PlainText
          text: "\u2191 " + Model.formatRate(root.uploadRate, Commons.I18n.formatNumber)
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }
      }

      Item { Layout.fillWidth: true; height: 1 }

      Column {
        Layout.alignment: Qt.AlignRight | Qt.AlignTop
        spacing: Style.space(1)

        Text {
          textFormat: Text.PlainText
          anchors.right: parent.right
          text: root.peakLabel + " " + Model.formatRate(root.peaks.max, Commons.I18n.formatNumber)
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }

        Text {
          textFormat: Text.PlainText
          anchors.right: parent.right
          text: root.scaleLabel + " " + Model.formatRate(root.targetCeiling, Commons.I18n.formatNumber)
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.45)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
    }

    // Dual-curve vector chart area.
    Item {
      id: plotArea
      width: parent.width
      height: Style.space(72)
      clip: true

      // Subtle horizontal reference grid lines.
      Repeater {
        model: [0.25, 0.5, 0.75, 1.0]
        Rectangle {
          required property real modelData
          width: plotArea.width
          height: 1
          y: Math.round(plotArea.height * modelData) - 1
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, modelData === 1.0 ? 0.12 : 0.06)
        }
      }

      Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer

        // Upload subtle fill.
        ShapePath {
          strokeWidth: 0
          fillGradient: LinearGradient {
            x1: 0; y1: 0
            x2: 0; y2: plotArea.height
            GradientStop { position: 0.0; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10) }
            GradientStop { position: 1.0; color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.0) }
          }
          PathSvg {
            path: root.buildLinePath("tx", plotArea.width, plotArea.height, true)
          }
        }

        // Download accent gradient fill.
        ShapePath {
          strokeWidth: 0
          fillGradient: LinearGradient {
            x1: 0; y1: 0
            x2: 0; y2: plotArea.height
            GradientStop { position: 0.0; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.30) }
            GradientStop { position: 1.0; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.01) }
          }
          PathSvg {
            path: root.buildLinePath("rx", plotArea.width, plotArea.height, true)
          }
        }

        // Upload stroke.
        ShapePath {
          strokeWidth: Math.max(1.2, Style.spaceReal(1.4))
          strokeColor: root.uploadStroke
          fillColor: "transparent"
          capStyle: ShapePath.RoundCap
          joinStyle: ShapePath.RoundJoin
          PathSvg {
            path: root.buildLinePath("tx", plotArea.width, plotArea.height, false)
          }
        }

        // Download accent stroke on top.
        ShapePath {
          strokeWidth: Math.max(1.6, Style.spaceReal(2.0))
          strokeColor: root.accent
          fillColor: "transparent"
          capStyle: ShapePath.RoundCap
          joinStyle: ShapePath.RoundJoin
          PathSvg {
            path: root.buildLinePath("rx", plotArea.width, plotArea.height, false)
          }
        }
      }
    }
  }
}
