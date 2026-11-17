/*
 * Precursor canvas SDK: `window.precursor`, for HTML canvases.
 *
 * A canvas page includes it with <script src="/canvas-sdk.js"></script>. It runs
 * same-origin inside the conversation's inline frame (CanvasBlock.tsx) and also
 * works when the page is opened on its own (then the theme follows the system
 * and `ask` has no message box to fill).
 *
 *   precursor.canvas                    { conversationId, name, path, kind } or null
 *   precursor.theme / onThemeChange(fn) "light" | "dark", and changes to it
 *   precursor.state.load() / save(obj)  JSON kept between visits
 *   precursor.ask(text)                 put text in the conversation's message box
 *   precursor.download(name, content, mime?) / exportSvg(svgEl, name)
 *   precursor.api(path, { method, body }) call the Precursor API (/api/...)
 *   precursor.resize()                  re-measure the frame height
 *
 * Theme colours are CSS variables on <html>: --pc-bg, --pc-surface, --pc-text,
 * --pc-muted, --pc-border, --pc-accent.
 */
(function () {
  "use strict";
  if (window.precursor) return;

  var TOKENS = ["bg", "surface", "text", "muted", "border", "accent"];
  var FALLBACK = {
    light: { bg: "#ffffff", surface: "#f7f7f8", text: "#111827", muted: "#6b7280", border: "#e5e7eb", accent: "#2563eb" },
    dark: { bg: "#0b0d10", surface: "#15181d", text: "#e6e8eb", muted: "#8a93a0", border: "#2a2f37", accent: "#60a5fa" },
  };
  var SUFFIXES = [["html", ".canvas.html"], ["drawio", ".drawio"], ["mermaid", ".mmd"], ["svg", ".svg"]];

  var embedded = window.parent !== window;
  var parentRoot = null;
  try {
    if (embedded) parentRoot = window.parent.document.documentElement;
  } catch (e) {
    parentRoot = null; // a different origin: theme falls back to the system
  }

  function readCanvas() {
    var m = location.pathname.match(/^\/raw\/canvas\/([0-9a-f-]{36})\/([^/]+)$/);
    if (!m) return null;
    var file = decodeURIComponent(m[2]);
    for (var i = 0; i < SUFFIXES.length; i++) {
      var suffix = SUFFIXES[i][1];
      if (file.toLowerCase().slice(-suffix.length) === suffix) {
        var name = file.slice(0, -suffix.length);
        return { conversationId: m[1], name: name, path: m[1] + "/" + file, kind: SUFFIXES[i][0] };
      }
    }
    return null;
  }

  // ---------------------------------------------------------------- theme
  var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  var listeners = [];
  var current = null;

  function currentTheme() {
    if (parentRoot) return parentRoot.classList.contains("dark") ? "dark" : "light";
    return media && media.matches ? "dark" : "light";
  }

  function applyTheme() {
    var theme = currentTheme();
    var root = document.documentElement;
    var styles = parentRoot ? window.parent.getComputedStyle(parentRoot) : null;
    TOKENS.forEach(function (token) {
      var value = styles && styles.getPropertyValue("--" + token).trim();
      root.style.setProperty("--pc-" + token, value || FALLBACK[theme][token]);
    });
    root.classList.toggle("dark", theme === "dark");
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    if (theme !== current) {
      current = theme;
      api.theme = theme;
      listeners.slice().forEach(function (fn) {
        try {
          fn(theme);
        } catch (e) {
          console.error(e);
        }
      });
    }
  }

  // ---------------------------------------------------------------- frame
  function post(message) {
    if (embedded) window.parent.postMessage(message, location.origin);
  }

  var lastHeight = 0;
  function resize() {
    var body = document.body;
    if (!body) return;
    var height = Math.ceil(
      Math.max(body.scrollHeight, body.offsetHeight, document.documentElement.offsetHeight),
    );
    if (Math.abs(height - lastHeight) < 2) return;
    lastHeight = height;
    post({ type: "precursor:canvas-resize", height: height });
  }

  // ---------------------------------------------------------------- api
  function request(path, options) {
    options = options || {};
    var init = { method: options.method || "GET", headers: { Accept: "application/json" } };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = typeof options.body === "string" ? options.body : JSON.stringify(options.body);
    }
    var url = path.charAt(0) === "/" ? path : "/api/" + path;
    return fetch(url, init).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (text) {
          throw new Error(res.status + " " + (text || res.statusText));
        });
      }
      if (res.status === 204) return null;
      var type = res.headers.get("content-type") || "";
      return type.indexOf("json") >= 0 ? res.json() : res.text();
    });
  }

  function statePath() {
    if (!api.canvas) throw new Error("precursor.state needs a canvas served from the canvas workspace");
    return (
      "/api/canvas/" + api.canvas.conversationId + "/" + encodeURIComponent(api.canvas.name) + "/state"
    );
  }

  function download(filename, content, mime) {
    var blob = content instanceof Blob ? content : new Blob([content], { type: mime || "text/plain" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  }

  var api = {
    version: 1,
    canvas: readCanvas(),
    embedded: embedded,
    theme: currentTheme(),
    onThemeChange: function (fn) {
      listeners.push(fn);
      return function () {
        listeners = listeners.filter(function (l) {
          return l !== fn;
        });
      };
    },
    state: {
      load: function () {
        return request(statePath()).then(function (r) {
          return r ? r.state : null;
        });
      },
      save: function (state) {
        return request(statePath(), { method: "PUT", body: { state: state } }).then(function () {
          return state;
        });
      },
    },
    ask: function (text) {
      if (!embedded) return false;
      post({ type: "precursor:canvas-ask", text: String(text) });
      return true;
    },
    download: download,
    exportSvg: function (svg, filename) {
      var clone = svg.cloneNode(true);
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      download(filename || "canvas.svg", new XMLSerializer().serializeToString(clone), "image/svg+xml");
    },
    api: request,
    resize: function () {
      lastHeight = 0;
      resize();
    },
  };
  window.precursor = api;

  applyTheme();
  if (parentRoot && window.MutationObserver) {
    new MutationObserver(applyTheme).observe(parentRoot, { attributes: true, attributeFilter: ["class", "style"] });
  } else if (media && media.addEventListener) {
    media.addEventListener("change", applyTheme);
  }

  function watchSize() {
    resize();
    if (window.ResizeObserver) new ResizeObserver(resize).observe(document.body);
    window.addEventListener("load", resize);
  }
  if (document.body) watchSize();
  else document.addEventListener("DOMContentLoaded", watchSize);
})();
