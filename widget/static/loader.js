/* static/loader.js
   ES5-only, no deps, no fetch.
*/
(function () {
  var scripts = document.getElementsByTagName("script");
  var scriptEl = document.currentScript || null;
  var i;

  if (!scriptEl) {
    for (i = 0; i < scripts.length; i++) {
      var s = scripts[i];
      var src = s.getAttribute("src") || "";
      if (src.indexOf("/loader.js") !== -1 && s.getAttribute("data-widget-key")) {
        scriptEl = s;
      }
    }
  }
  if (!scriptEl && scripts.length) {
    scriptEl = scripts[scripts.length - 1];
  }
  if (!scriptEl) {
    return;
  }

  var widgetKey = scriptEl.getAttribute("data-widget-key");
  if (!widgetKey) {
    if (window && window.console && console.error) {
      console.error("[widget-service] data-widget-key is required");
    }
    return;
  }

  var position = scriptEl.getAttribute("data-position") || "right";
  if (position !== "left" && position !== "right") {
    position = "right";
  }

  var marginStr = scriptEl.getAttribute("data-margin");
  var margin = 24;
  if (marginStr && !isNaN(parseInt(marginStr, 10))) {
    margin = parseInt(marginStr, 10);
  }

  var primaryColor = scriptEl.getAttribute("data-primary-color") || "#4f46e5";

  // Determine widget-service origin from script src
  var a = document.createElement("a");
  a.href = scriptEl.src;
  var serviceOrigin = a.protocol + "//" + a.host;
  // Fallback to current origin if host is empty (relative path case)
  if (!a.host || a.host.length === 0) {
    serviceOrigin = window.location.protocol + "//" + window.location.host;
  }

  var uid = "wsw_" + String(widgetKey).replace(/[^a-zA-Z0-9_]/g, "_") + "_" + String(Math.floor(Math.random() * 1000000));

  var btnId = uid + "_btn";
  var panelId = uid + "_panel";
  var iframeId = uid + "_iframe";

  function px(n) {
    return String(n) + "px";
  }

  function createButton() {
    var btn = document.createElement("div");
    btn.id = btnId;
    btn.setAttribute("role", "button");
    btn.setAttribute("aria-label", "Открыть чат");

    btn.style.position = "fixed";
    btn.style.bottom = px(margin);
    btn.style.width = "56px";
    btn.style.height = "56px";
    btn.style.borderRadius = "9999px";
    btn.style.background = primaryColor;
    btn.style.boxShadow = "0 10px 25px rgba(0,0,0,0.18)";
    btn.style.cursor = "pointer";
    btn.style.zIndex = "2147483647";
    btn.style.display = "flex";
    btn.style.alignItems = "center";
    btn.style.justifyContent = "center";
    btn.style.userSelect = "none";

    if (position === "left") {
      btn.style.left = px(margin);
    } else {
      btn.style.right = px(margin);
    }

    // Simple icon (chat bubble)
    var icon = document.createElement("div");
    icon.style.width = "22px";
    icon.style.height = "22px";
    icon.style.borderRadius = "6px";
    icon.style.background = "rgba(255,255,255,0.95)";
    icon.style.position = "relative";

    var dot1 = document.createElement("div");
    dot1.style.position = "absolute";
    dot1.style.left = "5px";
    dot1.style.top = "9px";
    dot1.style.width = "3px";
    dot1.style.height = "3px";
    dot1.style.borderRadius = "9999px";
    dot1.style.background = primaryColor;

    var dot2 = document.createElement("div");
    dot2.style.position = "absolute";
    dot2.style.left = "10px";
    dot2.style.top = "9px";
    dot2.style.width = "3px";
    dot2.style.height = "3px";
    dot2.style.borderRadius = "9999px";
    dot2.style.background = primaryColor;

    var dot3 = document.createElement("div");
    dot3.style.position = "absolute";
    dot3.style.left = "15px";
    dot3.style.top = "9px";
    dot3.style.width = "3px";
    dot3.style.height = "3px";
    dot3.style.borderRadius = "9999px";
    dot3.style.background = primaryColor;

    icon.appendChild(dot1);
    icon.appendChild(dot2);
    icon.appendChild(dot3);
    btn.appendChild(icon);

    return btn;
  }

  function createPanel() {
    var panel = document.createElement("div");
    panel.id = panelId;

    panel.style.position = "fixed";
    panel.style.bottom = px(margin + 68);
    panel.style.width = "440px";
    panel.style.height = "600px";
    panel.style.borderRadius = "16px";
    panel.style.overflow = "hidden";
    panel.style.boxShadow = "0 18px 45px rgba(0,0,0,0.22)";
    panel.style.background = "#ffffff";
    panel.style.zIndex = "2147483647";
    panel.style.display = "none";

    if (position === "left") {
      panel.style.left = px(margin);
    } else {
      panel.style.right = px(margin);
    }

    // Mobile-friendly: full screen
    if (window && window.matchMedia && window.matchMedia("(max-width: 480px)").matches) {
      panel.style.left = px(12);
      panel.style.right = px(12);
      panel.style.bottom = px(12);
      panel.style.width = "auto";
      panel.style.height = "75vh";
      panel.style.borderRadius = "16px";
    }

    var iframe = document.createElement("iframe");
    iframe.id = iframeId;
    iframe.setAttribute("title", "Chat Widget");
    iframe.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms");
    iframe.style.border = "0";
    iframe.style.width = "100%";
    iframe.style.height = "100%";
    iframe.style.display = "block";

    panel.appendChild(iframe);
    return panel;
  }

  var open = false;
  var btnEl = createButton();
  var panelEl = createPanel();
  var iframeEl = panelEl.getElementsByTagName("iframe")[0];

  function sendContext() {
    if (!iframeEl || !iframeEl.contentWindow) return;
    try {
      iframeEl.contentWindow.postMessage(
        {
          type: "WIDGET_CONTEXT",
          widget_key: widgetKey,
          page_url: String(window.location.href),
          page_title: String(document.title || ""),
          referrer: String(document.referrer || "")
        },
        serviceOrigin
      );
    } catch (e) {
      // ignore
    }
  }

  function ensureIframeSrc() {
    var want = serviceOrigin + "/chat?key=" + encodeURIComponent(widgetKey);
    if (!iframeEl.getAttribute("src")) {
      iframeEl.setAttribute("src", want);
    }
  }

  function openWidget() {
    if (open) return;
    open = true;
    ensureIframeSrc();
    panelEl.style.display = "block";
    sendContext();
  }

  function closeWidget() {
    open = false;
    panelEl.style.display = "none";
  }

  btnEl.onclick = function () {
    if (open) closeWidget();
    else openWidget();
  };

  iframeEl.onload = function () {
    sendContext();
    setTimeout(function () {
      sendContext();
    }, 400);
  };

  function onMessage(ev) {
    if (!ev || !ev.data) return;
    var data = ev.data;
    if (data.type === "WIDGET_CLOSE") {
      closeWidget();
      return;
    }
    if (data.type === "WIDGET_READY") {
      sendContext();
      return;
    }
  }

  if (window.addEventListener) {
    window.addEventListener("message", onMessage, false);
  } else if (window.attachEvent) {
    window.attachEvent("onmessage", onMessage);
  }

  document.body.appendChild(btnEl);
  document.body.appendChild(panelEl);
})();
