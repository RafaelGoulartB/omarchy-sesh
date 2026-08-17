import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  readonly property string binaryPath: Quickshell.env("HOME") + "/.local/bin/omarchy-sesh"
  readonly property string installPath: Qt.resolvedUrl("install.sh").toString().replace(/^file:\/\//, "")
  readonly property string manifestPath: Qt.resolvedUrl("manifest.json").toString().replace(/^file:\/\//, "")
  readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state"
  readonly property string installMarker: stateHome + "/omarchy/sesh-installed"

  property bool installed: false
  property string mode: "manual"
  property bool busy: checkProcess.running || installProcess.running || modeProcess.running || actionProcess.running
  property string status: ""
  property string error: ""
  property string pendingAction: ""
  property bool startEnabledMode: false

  function ensureInstalled() {
    if (checkProcess.running) return
    checkProcess.command = [
      "bash", "-c",
      "version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[\"version\"])' \"$3\") && [[ $(cat \"$1\" 2>/dev/null) == \"$version\" ]] && \"$2\" mode >/dev/null",
      "_", installMarker, binaryPath, manifestPath
    ]
    checkProcess.running = true
  }

  function install() {
    if (installProcess.running) return
    status = "Installing session manager..."
    error = ""
    installProcess.command = ["bash", installPath]
    installProcess.running = true
  }

  function refresh() {
    if (!installed || modeProcess.running) return
    modeProcess.command = [binaryPath, "mode"]
    modeProcess.running = true
  }

  function activate() {
    runMode("active")
  }

  function saveManual() {
    if (!installed || busy) return
    pendingAction = "save"
    runMode("manual")
  }

  function restore() {
    runAction("restore")
  }

  function runMode(nextMode) {
    if (!installed || busy) return
    status = nextMode === "active" ? "Enabling autosave..." : "Saving session..."
    error = ""
    modeProcess.command = [binaryPath, "mode", nextMode]
    modeProcess.running = true
  }

  function runAction(action) {
    if (!installed || busy) return
    status = action === "restore" ? "Restoring session..." : "Saving session..."
    error = ""
    actionProcess.command = action === "restore"
      ? [binaryPath, "restore"]
      : [binaryPath, "save", "--label", "manual"]
    actionProcess.running = true
  }

  Process {
    id: checkProcess
    command: []
    onExited: function(exitCode) {
      root.installed = exitCode === 0
      if (root.installed) root.refresh()
      else root.install()
    }
  }

  Process {
    id: installProcess
    command: []
    stderr: StdioCollector { id: installError; waitForEnd: true }
    onExited: function(exitCode) {
      root.installed = exitCode === 0
      root.status = exitCode === 0 ? "Session manager installed" : ""
      root.error = exitCode === 0 ? "" : (installError.text.trim() || "Installation failed")
      if (root.installed) {
        root.startEnabledMode = true
        root.refresh()
      }
    }
  }

  Process {
    id: modeProcess
    command: []
    stdout: StdioCollector { id: modeOutput; waitForEnd: true }
    stderr: StdioCollector { id: modeError; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.status = ""
        root.error = modeError.text.trim() || "Could not change autosave mode"
        root.pendingAction = ""
        return
      }
      var value = modeOutput.text.trim()
      if (value === "active" || value === "manual") root.mode = value
      if (root.startEnabledMode) {
        root.startEnabledMode = false
        if (value === "active") {
          Qt.callLater(function() { root.runMode("active") })
          return
        }
      }
      if (root.pendingAction === "save") {
        root.pendingAction = ""
        Qt.callLater(function() { root.runAction("save") })
      } else {
        root.status = value === "active" ? "Autosave active" : "Manual mode"
      }
    }
  }

  Process {
    id: actionProcess
    command: []
    stdout: StdioCollector { id: actionOutput; waitForEnd: true }
    stderr: StdioCollector { id: actionError; waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.status = actionOutput.text.trim() || "Session action completed"
        root.error = ""
      } else {
        root.status = ""
        root.error = actionError.text.trim() || actionOutput.text.trim() || "Session action failed"
      }
      root.refresh()
    }
  }
}
