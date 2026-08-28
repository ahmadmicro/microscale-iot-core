/* iot-core dashboard runtime.
 * Loads a declarative spec, connects to MQTT-over-WebSocket, binds widgets to canonical topics.
 * History charts pull from the historian read API (spec.api). Threshold alerts raise
 * notifications while the PWA is open. Role-based views: ?role=viewer hides operator
 * pages/cards and disables writes (UI scoping only — real enforcement is the broker ACL).
 */
(function () {
  "use strict";

  var spec, client, role;
  var cards = [];                 // {def, el, update(env), dispose?()}
  var byTopic = {};               // topic -> [cards]
  var history = {};               // topic -> [values] for sparklines
  var alertState = {};            // card title -> currently alerting?

  // ---- value formatting (driven by the spec's `format`, canonical units assumed) ----
  function fmtPower(w) {
    var a = Math.abs(w);
    if (a >= 1e6) return (w / 1e6).toFixed(2) + " MW";
    if (a >= 1e3) return (w / 1e3).toFixed(2) + " kW";
    return Math.round(w) + " W";
  }
  function fmtEnergy(wh) {
    var a = Math.abs(wh);
    if (a >= 1e6) return (wh / 1e6).toFixed(2) + " MWh";
    if (a >= 1e3) return (wh / 1e3).toFixed(2) + " kWh";
    return Math.round(wh) + " Wh";
  }
  function fmtValue(def, v) {
    switch (def.format) {
      case "power": return fmtPower(v);
      case "energy": return fmtEnergy(v);
      case "percent": return (v <= 1 ? v * 100 : v).toFixed(1) + "%";
      case "temperature": return (+v).toFixed(1) + " °C";
      case "number": return (+v).toFixed(2) + (def.unit ? " " + def.unit : "");
      default: return String(v);
    }
  }

  function sparkline(topic) {
    var vals = history[topic] || [];
    if (vals.length < 2) return "";
    var w = 140, h = 34, min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var span = (max - min) || 1;
    var pts = vals.map(function (v, i) {
      var x = (i / (vals.length - 1)) * w;
      var y = h - ((v - min) / span) * (h - 4) - 2;
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    return '<svg class="spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
      '<polyline fill="none" stroke="var(--brand)" stroke-width="2" points="' + pts + '"/></svg>';
  }

  // ---- alerts -> notification (rising edge only; needs the bell enabled) ----
  function notify(title, body) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    if (!navigator.serviceWorker) return;
    navigator.serviceWorker.getRegistration().then(function (reg) {
      if (reg) reg.showNotification("⚠ " + title, { body: body, icon: "icon.svg", tag: title });
    });
  }
  function checkAlert(def, el, env) {
    if (!def.alert) return;
    var frac = env.v <= 1 ? env.v : env.v / 100;   // normalized for `below` fraction alerts
    var active = (def.alert.below != null && frac < def.alert.below) ||
                 (def.alert.above != null && +env.v > def.alert.above);
    el.classList.toggle("alert", !!active);
    if (active && !alertState[def.title]) notify(def.title, fmtValue(def, env.v));
    alertState[def.title] = !!active;
  }

  // ---- card factories ----
  function makeMetric(def) {
    var el = card(def.title);
    var val = add(el, "div", "value", "—");
    var sparkHost = add(el, "div", "");
    return { def: def, el: el, update: function (env) {
      pushHistory(def.bind, env.v, def.history);
      val.innerHTML = fmtValue(def, env.v);
      staleness(val, env);
      if (def.history) sparkHost.innerHTML = sparkline(def.bind);
      checkAlert(def, el, env);
    }};
  }

  function makeGauge(def) {
    var el = card(def.title);
    var val = add(el, "div", "value", "—");
    var wrap = add(el, "div", "gauge-wrap");
    var bar = add(wrap, "div", "gauge-bar");
    var fill = add(bar, "div", "gauge-fill");
    var sub = add(wrap, "div", "sub", def.min + "–" + def.max);
    return { def: def, el: el, update: function (env) {
      val.innerHTML = (+env.v).toFixed(1) + '<span class="u">' + (env.u || "") + "</span>";
      var pct = Math.max(0, Math.min(1, (env.v - def.min) / (def.max - def.min)));
      fill.style.width = (pct * 100).toFixed(0) + "%";
      var warn = def.warn && (env.v < def.warn[0] || env.v > def.warn[1]);
      fill.classList.toggle("warn", !!warn);
      staleness(val, env);
    }};
  }

  function makeSourceSplit(def) {
    var el = card(def.title);
    var val = add(el, "div", "value", "—");
    var badge = add(el, "span", "badge", "…");
    return { def: def, el: el, update: function (env) {
      val.innerHTML = fmtPower(env.v);
      var src = env.src || "unknown";
      badge.textContent = src;
      badge.className = "badge " + src;
      staleness(val, env);
    }};
  }

  function canWrite(def) {
    var allowed = def.writeRoles || ["operator"];
    return allowed.indexOf(role) !== -1;
  }

  function makeSwitch(def) {
    var el = card(def.title);
    var row = add(el, "div", "switch");
    var label = add(row, "span", "sub", "—");
    var tog = add(row, "button", "toggle");
    var pending = false;
    if (!canWrite(def)) tog.classList.add("readonly");
    tog.addEventListener("click", function () {
      if (!client || pending || !canWrite(def)) return;
      var next = tog.classList.contains("on") ? "off" : "on";
      pending = true; tog.classList.add("pending");
      client.publish(def.write, JSON.stringify(next), { qos: 1 });
      setTimeout(function () { pending = false; tog.classList.remove("pending"); }, 3000);
    });
    return { def: def, el: el, update: function (env) {
      var on = env.v === "on" || env.v === 1 || env.v === true;
      tog.classList.toggle("on", on);
      tog.classList.remove("pending"); pending = false;
      label.textContent = on ? "ON" : "OFF";
      staleness(label, env);
    }};
  }

  // ---- history chart (historian read API) ----
  function makeChart(def) {
    var el = card(def.title); el.classList.add("wide");
    var val = add(el, "div", "value", "—");
    var host = add(el, "div", "chart-host");
    var meta = add(el, "div", "sub", "loading…");

    function draw(rows) {
      rows = rows.filter(function (r) { return r.avg != null; });
      if (!rows.length) { host.innerHTML = ""; meta.textContent = "no history yet"; return; }
      if (rows.length === 1) rows = [rows[0], rows[0]];  // single bucket -> flat line, not an invisible point
      var w = 600, h = 120, pad = 4;
      var lo = Math.min.apply(null, rows.map(function (r) { return r.min; }));
      var hi = Math.max.apply(null, rows.map(function (r) { return r.max; }));
      var span = (hi - lo) || 1, n = rows.length;
      function pt(v, i) {
        var x = pad + (n > 1 ? (i / (n - 1)) * (w - 2 * pad) : (w - 2 * pad) / 2);
        var y = h - pad - ((v - lo) / span) * (h - 2 * pad);
        return x.toFixed(1) + "," + y.toFixed(1);
      }
      var line = rows.map(function (r, i) { return pt(r.avg, i); }).join(" ");
      var band = rows.map(function (r, i) { return pt(r.max, i); })
        .concat(rows.slice().reverse().map(function (r, i) { return pt(r.min, n - 1 - i); }))
        .join(" ");
      host.innerHTML =
        '<svg class="chart" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none">' +
        '<polygon class="chart-band" points="' + band + '"/>' +
        '<polyline class="chart-line" fill="none" stroke-width="2" points="' + line + '"/></svg>';
      val.innerHTML = fmtValue(def, rows[n - 1].avg);
      meta.textContent = fmtValue(def, lo) + " – " + fmtValue(def, hi) +
        " · last " + (def.hours || 24) + "h · " + (def.bucket || "15 minutes") + " buckets";
    }

    function refresh() {
      if (!spec.api) { meta.textContent = "no historian api configured"; return; }
      var end = new Date(), start = new Date(end.getTime() - (def.hours || 24) * 3600e3);
      var q = "metric=" + encodeURIComponent(def.metric) +
        "&device=" + encodeURIComponent(def.device) +
        "&channel=" + encodeURIComponent(def.channel || "main") +
        "&bucket=" + encodeURIComponent(def.bucket || "15 minutes") +
        "&start=" + start.toISOString() + "&end=" + end.toISOString();
      fetch(spec.api + "/" + spec.site + "/readings?" + q)
        .then(function (r) { return r.json(); })
        .then(function (d) { draw(d.rows || []); })
        .catch(function () { meta.textContent = "history unavailable"; });
    }

    refresh();
    var timer = setInterval(refresh, (def.refresh || 60) * 1000);
    return { def: def, el: el, update: function () {},
             dispose: function () { clearInterval(timer); } };
  }

  var FACTORY = { metric: makeMetric, gauge: makeGauge, "source-split": makeSourceSplit,
                  switch: makeSwitch, chart: makeChart };

  // ---- dom helpers ----
  function card(title) {
    var el = document.createElement("div"); el.className = "card";
    add(el, "div", "title", title);
    return el;
  }
  function add(parent, tag, cls, text) {
    var e = document.createElement(tag); if (cls) e.className = cls;
    if (text != null) e.textContent = text; parent.appendChild(e); return e;
  }
  function pushHistory(topic, v, keep) {
    var n = parseInt(keep, 10) || 60;
    (history[topic] = history[topic] || []).push(v);
    if (history[topic].length > n) history[topic].shift();
  }
  function staleness(node, env) { node.classList.toggle("stale", env.q === "stale" || env.q === "bad"); }
  function visibleTo(def) { return !def.roles || def.roles.indexOf(role) !== -1; }

  // ---- pages ----
  var pageIdx = [];               // rendered tab order -> spec.pages index

  function renderPage(idx) {
    var grid = document.getElementById("grid"); grid.innerHTML = "";
    cards.forEach(function (c) { if (c.dispose) c.dispose(); });
    cards = []; byTopic = {};
    spec.pages[idx].cards.filter(visibleTo).forEach(function (def) {
      var f = FACTORY[def.type]; if (!f) return;
      var c = f(def); grid.appendChild(c.el); cards.push(c);
      if (def.bind) (byTopic[def.bind] = byTopic[def.bind] || []).push(c);
    });
    if (client && client.connected) subscribeAll();
    document.querySelectorAll("#tabs button").forEach(function (b, i) {
      b.classList.toggle("active", pageIdx[i] === idx);
    });
  }

  function subscribeAll() {
    Object.keys(byTopic).forEach(function (t) { client.subscribe(t, { qos: 1 }); });
  }

  // ---- boot ----
  function conn(state, text) {
    var c = document.getElementById("conn");
    c.className = "conn " + state; c.textContent = text;
  }

  function applyTheme(theme) {
    if (!theme) return;
    if (theme.brand) document.documentElement.style.setProperty("--brand", theme.brand);
    if (theme.mode === "dark") document.documentElement.classList.add("dark");
    if (theme.logo) {
      var img = document.createElement("img");
      img.src = theme.logo; img.alt = ""; img.className = "logo";
      var h1 = document.getElementById("site-title");
      h1.parentNode.insertBefore(img, h1);
    }
  }

  function setupBell() {
    if (!("Notification" in window)) return;
    var bell = document.getElementById("bell");
    bell.hidden = false;
    function paint() { bell.classList.toggle("on", Notification.permission === "granted"); }
    bell.addEventListener("click", function () {
      Notification.requestPermission().then(paint);
    });
    paint();
  }

  fetch("dashboard.json").then(function (r) { return r.json(); }).then(function (s) {
    spec = s;
    role = new URLSearchParams(location.search).get("role") || spec.defaultRole || "operator";
    applyTheme(spec.theme);
    document.getElementById("site-title").textContent = spec.title;
    setupBell();
    var tabs = document.getElementById("tabs");
    spec.pages.forEach(function (p, i) {
      if (!visibleTo(p)) return;
      pageIdx.push(i);
      var b = document.createElement("button"); b.textContent = p.name;
      b.addEventListener("click", function () { renderPage(i); }); tabs.appendChild(b);
    });
    renderPage(pageIdx[0] != null ? pageIdx[0] : 0);

    client = mqtt.connect(spec.broker, { reconnectPeriod: 2000 });
    client.on("connect", function () { conn("on", "live"); subscribeAll(); });
    client.on("reconnect", function () { conn("off", "reconnecting…"); });
    client.on("close", function () { conn("off", "offline"); });
    client.on("message", function (topic, payload) {
      var env; try { env = JSON.parse(payload.toString()); } catch (e) { return; }
      (byTopic[topic] || []).forEach(function (c) { c.update(env); });
    });
  }).catch(function (e) { conn("off", "spec error"); console.error(e); });
})();
