import QtQuick

Item {
  id: root

  property real iconSize: 18
  property color primary: "white"
  property color inverse: "black"
  property bool active: false

  implicitWidth: iconSize
  implicitHeight: iconSize

  onPrimaryChanged: canvas.requestPaint()
  onInverseChanged: canvas.requestPaint()
  onActiveChanged: canvas.requestPaint()
  onIconSizeChanged: canvas.requestPaint()

  Canvas {
    id: canvas
    anchors.fill: parent

    onPaint: {
      var ctx = getContext("2d")
      var s = width / 20
      ctx.reset()
      ctx.clearRect(0, 0, width, height)
      ctx.scale(s, s)

      ctx.beginPath()
      ctx.moveTo(3, 3)
      ctx.lineTo(17, 3)
      ctx.lineTo(19, 7)
      ctx.lineTo(10, 18)
      ctx.lineTo(1, 7)
      ctx.closePath()
      ctx.fillStyle = root.active ? root.primary : root.inverse
      ctx.fill()
      ctx.strokeStyle = root.active ? root.inverse : root.primary
      ctx.lineWidth = 1.35
      ctx.stroke()

      ctx.beginPath()
      ctx.moveTo(14.8, 6.2)
      ctx.bezierCurveTo(12.8, 4.7, 7.1, 4.8, 6.1, 7.1)
      ctx.bezierCurveTo(5.2, 9.2, 8.4, 9.8, 10.4, 10.1)
      ctx.bezierCurveTo(12.4, 10.5, 12.5, 12.2, 9.8, 14.6)
      ctx.moveTo(5.7, 12.8)
      ctx.bezierCurveTo(7.4, 14.5, 11.9, 14.7, 13.6, 12.4)
      ctx.strokeStyle = root.active ? root.inverse : root.primary
      ctx.lineWidth = 2.1
      ctx.lineCap = "round"
      ctx.stroke()
    }
  }
}
