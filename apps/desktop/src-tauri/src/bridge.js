// GPU Deck desktop bridge (injected into the main window's top frame).
// Exposes window.__GPUDECK_DESKTOP__ only on the connected server's origin.
// The IPC ACL allows exactly two commands from that origin: notify and
// set_tray_status. The web app's lib/desktop module is the only caller.
(function () {
  "use strict";
  var ORIGIN = __ORIGIN__;
  try {
    if (window.top !== window) return;
    if (window.location.origin !== ORIGIN) return;
    if (Object.prototype.hasOwnProperty.call(window, "__GPUDECK_DESKTOP__")) return;
  } catch (_) {
    return;
  }
  function invoke(cmd, payload) {
    var ipc = window.__TAURI_INTERNALS__;
    if (!ipc || typeof ipc.invoke !== "function") {
      return Promise.reject(new Error("GPU Deck desktop IPC is unavailable"));
    }
    return ipc.invoke(cmd, { payload: payload });
  }
  var api = Object.freeze({
    version: 1,
    appVersion: __VERSION__,
    platform: __PLATFORM__,
    notify: function (notification) {
      return invoke("notify", notification);
    },
    setTrayStatus: function (status) {
      return invoke("set_tray_status", status);
    },
  });
  Object.defineProperty(window, "__GPUDECK_DESKTOP__", {
    value: api,
    enumerable: false,
    configurable: false,
    writable: false,
  });
})();
