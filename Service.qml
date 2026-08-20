import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  readonly property string binaryPath: Quickshell.env("HOME") + "/.local/bin/omarchy-sesh"
  readonly property string installPath: Qt.resolvedUrl("install.sh").toString().replace(/^file:\/\//, "")
  readonly property string manifestPath: Qt.resolvedUrl("manifest.json").toString().replace(/^file:\/\//, "")
  readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state"
  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config"
  readonly property string unitDir: configHome + "/systemd/user"
  readonly property string installMarker: stateHome + "/omarchy/sesh-installed"

  property bool installed: false
  property string mode: "manual"
  property bool modeKnown: false
  property bool busy: checkProcess.running || installProcess.running || modeProcess.running || actionProcess.running || listProcess.running
  property string status: ""
  property string error: ""
  property var sessions: []
  property bool sessionsLoading: false
  property string pendingAction: ""
  property bool startEnabledMode: false
  property bool installAfterCheck: false
  property bool preserveStatus: false

  Component.onCompleted: ensureInstalled(false)

  function ensureInstalled(installIfMissing) {
    if (installIfMissing === undefined) installIfMissing = true
    installAfterCheck = installAfterCheck || installIfMissing
    if (checkProcess.running) return
    checkProcess.command = [
      "bash", "-c",
      "version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[\"version\"])' \"$3\") && [[ $(cat \"$1\" 2>/dev/null) == \"$version\" ]] && [[ -x \"$2\" && -f \"$4/omarchy-sesh.service\" && -f \"$4/omarchy-sesh-autosave.service\" ]] && systemctl --user is-enabled omarchy-sesh.service >/dev/null && \"$2\" mode >/dev/null",
      "_", installMarker, binaryPath, manifestPath, unitDir
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

  function refresh(keepStatus) {
    if (!installed) return
    if (keepStatus === true) preserveStatus = true
    if (modeProcess.running) return
    if (keepStatus !== true) preserveStatus = false
    modeProcess.command = [binaryPath, "mode"]
    modeProcess.running = true
  }

  function activate() {
    return runMode("active")
  }

  function saveManual() {
    if (!installed || busy) return false
    pendingAction = "save"
    return runMode("manual")
  }

  function restore() {
    return runAction("restore")
  }

  function listSessions() {
    if (!installed || listProcess.running || actionProcess.running) return false
    sessions = []
    sessionsLoading = true
    status = "Loading saved sessions..."
    error = ""
    listProcess.command = [binaryPath, "list", "--json"]
    listProcess.running = true
    return true
  }

  function restoreNamed(name) {
    return runAction("restore", name)
  }

  function runMode(nextMode) {
    if (!installed || busy) return false
    status = nextMode === "active" ? "Enabling autosave..." : "Saving session..."
    error = ""
    modeProcess.command = [binaryPath, "mode", nextMode]
    modeProcess.running = true
    return true
  }

  function runAction(action, name) {
    if (!installed || busy) return false
    status = action === "restore" ? "Restoring session..." : "Saving session..."
    error = ""
    actionProcess.command = action === "restore"
      ? (name === undefined ? [binaryPath, "restore"] : [binaryPath, "restore", "--name", name])
      : [binaryPath, "save", "--label", "manual"]
    actionProcess.running = true
    return true
  }

  Process {
    id: checkProcess
    command: []
    onExited: function(exitCode) {
      var shouldInstall = root.installAfterCheck
      root.installAfterCheck = false
      root.installed = exitCode === 0
      if (root.installed) root.refresh()
      else if (shouldInstall) root.install()
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
        if (root.preserveStatus) {
          root.preserveStatus = false
          return
        }
        root.preserveStatus = false
        root.status = ""
        root.error = modeError.text.trim() || "Could not change autosave mode"
        root.pendingAction = ""
        return
      }
      var output = modeOutput.text.trim().split(/\s+/)
      var value = output.length ? output[output.length - 1] : ""
      if (value !== "active" && value !== "manual") {
        root.preserveStatus = false
        root.status = ""
        root.error = "Unexpected autosave mode response"
        root.pendingAction = ""
        return
      }
      root.mode = value
      root.modeKnown = true
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
      } else if (!root.preserveStatus) {
        root.status = value === "active" ? "Autosave active" : "Manual mode"
      }
      root.preserveStatus = false
    }
  }

  Process {
    id: listProcess
    command: []
    stdout: StdioCollector { id: listOutput; waitForEnd: true }
    stderr: StdioCollector { id: listError; waitForEnd: true }
    onExited: function(exitCode) {
      root.sessionsLoading = false
      if (exitCode !== 0) {
        root.status = ""
        root.error = listError.text.trim() || "Could not load saved sessions"
        return
      }
      try {
        var sessions = JSON.parse(listOutput.text)
        if (!Array.isArray(sessions)) throw new Error("not an array")
        root.sessions = sessions
        root.status = sessions.length ? "Choose a saved session" : "No named sessions saved"
      } catch (error) {
        root.sessions = []
        root.status = ""
        root.error = "Could not read saved sessions"
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
      root.refresh(true)
    }
  }
}
