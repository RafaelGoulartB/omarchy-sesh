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

  property int selectedIndex: 0
  property bool cursorActive: false
  property bool showingSessions: false

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color inverse: Color.background
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool active: service.mode === "active"
  readonly property bool modeKnown: service.modeKnown
  readonly property var options: [
    { title: "Active", detail: "Enable automatic session snapshots", icon: "󰐊" },
    { title: "Manual", detail: "Disable autosave and save now", icon: "󰆓" },
    { title: "Restore", detail: "Choose a saved session to restore", icon: "󰑓" }
  ]

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function choose(index) {
    if (!service.installed || service.busy) return
    selectedIndex = index
    if (index === 0) service.activate()
    else if (index === 1) service.saveManual()
    else {
      showingSessions = true
      selectedIndex = 0
      cursorActive = false
      service.listSessions()
    }
  }

  function chooseSession(index) {
    if (!service.installed || service.busy || index < 0 || index >= service.sessions.length) return
    selectedIndex = index
    service.restoreNamed(service.sessions[index].name)
  }

  onOpenedChanged: if (opened) {
    selectedIndex = active ? 0 : 1
    cursorActive = false
    showingSessions = false
    service.ensureInstalled(true)
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
        else if (dy !== 0) {
          var count = root.showingSessions ? service.sessions.length : root.options.length
          if (count > 0) root.selectedIndex = (root.selectedIndex + dy + count) % count
        }
      }
      onActivateRequested: if (root.cursorActive) {
        if (root.showingSessions) root.chooseSession(root.selectedIndex)
        else root.choose(root.selectedIndex)
      }
      onReturnRequested: if (root.cursorActive) {
        if (root.showingSessions) root.chooseSession(root.selectedIndex)
        else root.choose(root.selectedIndex)
      }
      onCloseRequested: {
        if (root.showingSessions) {
          root.showingSessions = false
          root.cursorActive = false
          root.selectedIndex = 2
        } else root.close()
      }
      onTabRequested: function(direction) { root.switchPanel(direction) }

      ColumnLayout {
        id: column
        anchors.fill: parent
        spacing: Style.space(12)

        PanelHero {
          Layout.fillWidth: true
          title: root.showingSessions ? "Restore Session" : "Omarchy Sesh"
          meta: root.showingSessions ? "Named saved sessions" : (!root.modeKnown ? "Status unavailable" : (root.active ? "Autosave active" : "Manual mode"))
          detail: service.sessionsLoading ? "Loading saved sessions..." : (service.busy ? "Working..." : (root.showingSessions ? "Select a session to restore" : "Session management"))
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
          model: root.showingSessions ? [] : root.options

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

        Text {
          visible: root.showingSessions && !service.sessionsLoading && service.sessions.length === 0 && service.error === ""
          Layout.fillWidth: true
          text: "Named sessions are created with omarchy-sesh save --name NAME."
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }

        Repeater {
          model: root.showingSessions ? service.sessions : []

          CursorSurface {
            required property var modelData
            required property int index

            Layout.fillWidth: true
            implicitHeight: sessionRow.implicitHeight + Style.spacing.rowPaddingX
            foreground: root.foreground
            hasCursor: root.cursorActive && root.selectedIndex === index

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              enabled: service.installed && !service.busy
              cursorShape: Qt.PointingHandCursor
              onEntered: { root.cursorActive = true; root.selectedIndex = index }
              onClicked: root.chooseSession(index)
            }

            RowLayout {
              id: sessionRow
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(10)
              spacing: Style.space(10)

              Text {
                text: "󰑓"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.icon
              }

              ColumnLayout {
                Layout.fillWidth: true
                spacing: Style.space(1)

                Text {
                  Layout.fillWidth: true
                  text: modelData.name
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                  elide: Text.ElideRight
                }

                Text {
                  Layout.fillWidth: true
                  text: modelData.created_at + " | " + modelData.windows + (modelData.windows === 1 ? " window" : " windows")
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                }
              }
            }
          }
        }
      }
    }
  }
}
