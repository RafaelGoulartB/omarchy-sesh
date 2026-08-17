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

    function roundedRect(ctx, x, y, w, h, r) {
      ctx.moveTo(x + r, y)
      ctx.arcTo(x + w, y, x + w, y + h, r)
      ctx.arcTo(x + w, y + h, x, y + h, r)
      ctx.arcTo(x, y + h, x, y, r)
      ctx.arcTo(x, y, x + w, y, r)
      ctx.closePath()
    }

    onPaint: {
      var ctx = getContext("2d")
      var s = width / 20
      ctx.reset()
      ctx.clearRect(0, 0, width, height)
      ctx.scale(s, s)

      var fill = root.active ? root.primary : root.inverse
      var stroke = root.active ? root.inverse : root.primary

      ctx.beginPath()
      roundedRect(ctx, 2.75, 3.5, 12.5, 10.5, 1.7)
      ctx.strokeStyle = root.primary
      ctx.lineWidth = 1.2
      ctx.lineJoin = "round"
      ctx.stroke()

      ctx.beginPath()
      roundedRect(ctx, 6.75, 7.0, 12.5, 10.5, 1.7)
      ctx.fillStyle = fill
      ctx.fill()
      ctx.strokeStyle = stroke
      ctx.lineWidth = 1.35
      ctx.stroke()
    }
  }
}
