import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// LLM quota pill. Reads the state file written by `monitor.py --daemon`
// (llm-quota-balance-monitor). Middle-click forces a one-shot refresh.
BarWidget {
  id: root
  moduleName: "shinonome.llm-quota"

  readonly property string home: Quickshell.env("HOME")
  readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") !== "" ? Quickshell.env("XDG_STATE_HOME") : home + "/.local/state") + "/llm-quota-monitor/state.json"
  readonly property string monitorDir: home + "/Projects/llm-quota-balance-monitor"

  property var st: ({})
  property bool expanded: hoverArea.containsMouse

  FileView {
    path: root.statePath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.st = JSON.parse(String(text() || "")) } catch (e) { root.st = ({}) }
    }
    onLoadFailed: root.st = ({})
  }

  visible: (root.st.text || "") !== ""
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Behavior on implicitWidth { NumberAnimation { duration: 150 } }

  Process { id: refreshProc }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // Collapsed by default: icon only; hover expands the full pill text.
    text: root.expanded ? (root.st.text || "") : "󰚩"
    labelVisible: true
    hasVisualContent: text !== ""
    horizontalMargin: 8.75
    verticalPadding: 8.75
    tooltipText: (root.st.tooltip || "LLM quota: no data yet") + "\n(middle-click to refresh)"

    onPressed: function(b) {
      if (b === Qt.MiddleButton && !refreshProc.running) {
        refreshProc.command = ["/usr/bin/python3", root.monitorDir + "/monitor.py", "--once"]
        refreshProc.running = true
      }
    }
  }

  HoverHandler { id: hoverArea }
}
