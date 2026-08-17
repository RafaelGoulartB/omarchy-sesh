import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "mrpbennett.sesh"
  ipcTarget: "mrpbennett.sesh"
  manageIpc: false

  property bool firstOpen: true
  property int selectedIndex: 0
  property bool cursorActive: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color inverse: Color.background
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool active: service.mode === "active"
  readonly property bool modeKnown: service.modeKnown
  readonly property var options: [
    { title: "Active", detail: "Enable automatic session snapshots", icon: "󰐊" },
    { title: "Manual", detail: "Disable autosave and save now", icon: "󰆓" },
    { title: "Restore", detail: "Restore the latest saved session", icon: "󰑓" }
  ]

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function choose(index) {
    if (!service.installed || service.busy) return
    selectedIndex = index
    if (index === 0) service.activate()
    else if (index === 1) service.saveManual()
    else service.restore()
  }

  onOpenedChanged: if (opened) {
    selectedIndex = active ? 0 : 1
    cursorActive = false
    if (firstOpen) {
      firstOpen = false
      if (service.installed) service.refresh()
      else service.ensureInstalled()
    } else if (!service.installed) {
      service.ensureInstalled()
    } else {
      service.refresh()
    }
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }
  onActiveChanged: if (opened && !cursorActive) selectedIndex = active ? 0 : 1

  Service { id: service }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function active(): string { return service.activate() ? "ok" : "unavailable" }
    function manual(): string { return service.saveManual() ? "ok" : "unavailable" }
    function restore(): string { return service.restore() ? "ok" : "unavailable" }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    iconComponent: Component {
      Item {
        SessionIcon {
          anchors.centerIn: parent
          iconSize: Style.space(13)
          primary: root.barForeground
          inverse: Color.background
          active: root.active
          opacity: root.modeKnown ? 1.0 : 0.55
        }
      }
    }
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) service.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(460))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: false
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) root.cursorActive = true
        else if (dy !== 0) root.selectedIndex = (root.selectedIndex + dy + root.options.length) % root.options.length
      }
      onActivateRequested: if (root.cursorActive) root.choose(root.selectedIndex)
      onReturnRequested: if (root.cursorActive) root.choose(root.selectedIndex)
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      ColumnLayout {
        id: column
        anchors.fill: parent
        spacing: Style.space(12)

        PanelHero {
          Layout.fillWidth: true
          title: "Omarchy Sesh"
          meta: !root.modeKnown ? "Status unavailable" : (root.active ? "Autosave active" : "Manual mode")
          detail: service.busy ? "Working..." : "Session management"
          foreground: root.foreground
          fontFamily: root.fontFamily
          iconOpacity: service.installed ? 1.0 : 0.6
          iconComponent: Component {
            SessionIcon {
              iconSize: Style.font.display
              primary: root.foreground
              inverse: root.inverse
              active: root.active
            }
          }
        }

        Text {
          visible: service.status !== "" || service.error !== ""
          Layout.fillWidth: true
          text: service.error !== "" ? service.error : service.status
          color: service.error !== "" ? Color.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        PanelSeparator {
          Layout.fillWidth: true
          foreground: root.foreground
        }

        Repeater {
          model: root.options

          CursorSurface {
            required property var modelData
            required property int index

            Layout.fillWidth: true
            implicitHeight: optionRow.implicitHeight + Style.spacing.rowPaddingX
            foreground: root.foreground
            hasCursor: root.cursorActive && root.selectedIndex === index
            current: root.modeKnown && ((index === 0 && root.active) || (index === 1 && !root.active))

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              enabled: service.installed && !service.busy
              cursorShape: Qt.PointingHandCursor
              onEntered: { root.cursorActive = true; root.selectedIndex = index }
              onClicked: root.choose(index)
            }

            RowLayout {
              id: optionRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(10)
              spacing: Style.space(10)

              Text {
                text: modelData.icon
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.icon
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(1)

                Text {
                  Layout.fillWidth: true
                  text: modelData.title
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }

                Text {
                  Layout.fillWidth: true
                  text: modelData.detail
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }
        }
      }
    }
  }
}
