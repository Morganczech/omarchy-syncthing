import QtQuick
import QtQuick.Shapes
import qs.Commons
import qs.Commons as Commons

Item {
  id: root

  property real iconSize: Style.font.icon
  property color color: Commons.Color.foreground
  property color badgeColor: Commons.Color.urgent
  property bool syncing: false
  property bool warning: false
  property bool paused: false

  width: iconSize
  height: iconSize
  implicitWidth: iconSize
  implicitHeight: iconSize

  readonly property real cx: width / 2
  readonly property real cy: height / 2
  readonly property real ringRadius: Math.max(2, width * 0.39)
  readonly property real strokePx: Math.max(1.35, width * 0.088)
  readonly property real hubRadius: Math.max(1.5, width * 0.115)
  readonly property real nodeRadius: Math.max(1.3, width * 0.098)

  // Outer Syncthing ring stays stationary so optical bounds never wobble.
  Shape {
    anchors.fill: parent
    preferredRendererType: Shape.CurveRenderer

    ShapePath {
      strokeWidth: root.strokePx
      strokeColor: root.color
      fillColor: "transparent"
      capStyle: ShapePath.RoundCap

      PathAngleArc {
        centerX: root.cx
        centerY: root.cy
        radiusX: root.ringRadius
        radiusY: root.ringRadius
        startAngle: 0
        sweepAngle: 360
      }
    }
  }

  // Inner 3-spoke mesh rotates smoothly while actively synchronizing.
  Item {
    id: rotor
    anchors.fill: parent

    readonly property real hubOffsetX: root.width * 0.04
    readonly property real hubOffsetY: root.height * 0.03
    readonly property real hx: root.cx + ( rotorSpin.running ? 0 : hubOffsetX )
    readonly property real hy: root.cy + ( rotorSpin.running ? 0 : hubOffsetY )

    readonly property real a1: -32 * Math.PI / 180
    readonly property real a2: 96 * Math.PI / 180
    readonly property real a3: 208 * Math.PI / 180

    readonly property real x1: root.cx + root.ringRadius * Math.cos(a1)
    readonly property real y1: root.cy + root.ringRadius * Math.sin(a1)
    readonly property real x2: root.cx + root.ringRadius * Math.cos(a2)
    readonly property real y2: root.cy + root.ringRadius * Math.sin(a2)
    readonly property real x3: root.cx + root.ringRadius * Math.cos(a3)
    readonly property real y3: root.cy + root.ringRadius * Math.sin(a3)

    NumberAnimation on rotation {
      id: rotorSpin
      from: 0
      to: 360
      duration: Style.duration(1600)
      loops: Animation.Infinite
      easing.type: Easing.Linear
      running: root.syncing && root.visible && !Style.reduceMotion
      onStopped: rotor.rotation = 0
    }

    Shape {
      anchors.fill: parent
      preferredRendererType: Shape.CurveRenderer

      // Three spokes from hub to perimeter nodes.
      ShapePath {
        strokeWidth: root.strokePx * 0.92
        strokeColor: root.color
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap

        PathMove { x: rotor.hx; y: rotor.hy }
        PathLine { x: rotor.x1; y: rotor.y1 }
        PathMove { x: rotor.hx; y: rotor.hy }
        PathLine { x: rotor.x2; y: rotor.y2 }
        PathMove { x: rotor.hx; y: rotor.hy }
        PathLine { x: rotor.x3; y: rotor.y3 }
      }

      // Central hub + three satellite nodes.
      NodeDot { dotX: rotor.hx; dotY: rotor.hy; dotR: root.hubRadius }
      NodeDot { dotX: rotor.x1; dotY: rotor.y1; dotR: root.nodeRadius }
      NodeDot { dotX: rotor.x2; dotY: rotor.y2; dotR: root.nodeRadius }
      NodeDot { dotX: rotor.x3; dotY: rotor.y3; dotR: root.nodeRadius }
    }
  }

  // Diagonal slash when paused/offline-guarded, or urgent dot when attention needed.
  Rectangle {
    visible: root.warning
    width: Math.max(4, Math.round(root.width * 0.28))
    height: width
    radius: width / 2
    color: root.badgeColor
    anchors.right: parent.right
    anchors.bottom: parent.bottom
  }

  Rectangle {
    visible: root.paused && !root.warning
    width: Math.max(4, Math.round(root.width * 0.26))
    height: width
    radius: width / 2
    color: Qt.rgba(root.color.r, root.color.g, root.color.b, 0.85)
    anchors.right: parent.right
    anchors.bottom: parent.bottom
  }

  component NodeDot: ShapePath {
    property real dotX: 0
    property real dotY: 0
    property real dotR: 2

    strokeWidth: 0
    fillColor: root.color

    PathAngleArc {
      centerX: dotX
      centerY: dotY
      radiusX: dotR
      radiusY: dotR
      startAngle: 0
      sweepAngle: 360
    }
  }
}
