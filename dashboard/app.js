/* iot-core dashboard runtime.
 * Loads a declarative spec, connects to MQTT-over-WebSocket, binds widgets to canonical topics.
 * The successor to NodeWire's expr:/action: engine — declarative, no arbitrary code eval.
 */
(function () {
  "use strict";

  var spec, client;
  var cards = [];                 // {def, el, update(env)}
  var byTopic = {};               // topic -> [cards]
  var history = {};               // topic -> [values] for sparklines

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

  // ---- card factories ----
  function makeMetric(def) {
    var el = card(def.title);
    var val = add(el, "div", "value", "—");
    var sparkHost = add(el, "div", "");
    return { def: def, el: el, update: function (env) {
      pushHistory(def.bind, env.v, def.history);
      val.innerHTML = fmtValue(def, env.v) + (def.format === "number" && !def.unit ? "" : "");
      staleness(val, env);
      if (def.history) sparkHost.innerHTML = sparkline(def.bind);
      if (def.alert && def.alert.below != null) {
        var frac = env.v <= 1 ? env.v : env.v / 100;
        el.classList.toggle("alert", frac < def.alert.below);
      }
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

  function makeSwitch(def) {
    var el = card(def.title);
    var row = add(el, "div", "switch");
    var label = add(row, "span", "sub", "—");
    var tog = add(row, "button", "toggle");
    var pending = false;
    tog.addEventListener("click", function () {
      if (!client || pending) return;
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

  var FACTORY = { metric: makeMetric, gauge: makeGauge, "source-split": makeSourceSplit, switch: makeSwitch };

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

  // ---- pages ----
  function renderPage(idx) {
    var grid = document.getElementById("grid"); grid.innerHTML = "";
    cards = []; byTopic = {};
    spec.pages[idx].cards.forEach(function (def) {
      var f = FACTORY[def.type]; if (!f) return;
      var c = f(def); grid.appendChild(c.el); cards.push(c);
      (byTopic[def.bind] = byTopic[def.bind] || []).push(c);
    });
    if (client && client.connected) subscribeAll();
    document.querySelectorAll("#tabs button").forEach(function (b, i) {
      b.classList.toggle("active", i === idx);
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

  fetch("dashboard.json").then(function (r) { return r.json(); }).then(function (s) {
    spec = s;
    if (spec.theme && spec.theme.brand) document.documentElement.style.setProperty("--brand", spec.theme.brand);
    document.getElementById("site-title").textContent = spec.title;
    var tabs = document.getElementById("tabs");
    spec.pages.forEach(function (p, i) {
      var b = document.createElement("button"); b.textContent = p.name;
      b.addEventListener("click", function () { renderPage(i); }); tabs.appendChild(b);
    });
    renderPage(0);

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
