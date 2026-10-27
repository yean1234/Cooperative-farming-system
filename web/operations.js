"use strict";

/*
 * 공동영농 필지 최적화 — 서비스 초안 화면
 * 서버 API: GET /api/defaults, POST /api/scenario/demo, POST /api/scenario/import, POST /api/optimize
 */

const COOP = "COOP";
const CROPS = ["벼", "가루쌀", "콩", "조사료"];
const WATER = { 벼: "wet", 가루쌀: "wet", 콩: "dry", 조사료: "dry", 시설채소: "none" };
const SCEN = [
  { key: "S0", short: "개별영농", long: "개별영농 (현황)" },
  { key: "S1", short: "공동영농", long: "공동영농 · 교환 없음" },
  { key: "S2", short: "1:1 교환", long: "공동영농 + 1:1 교환" },
  { key: "S3", short: "다자간 교환", long: "공동영농 + 다자간 교환 (제안)" },
];
const DRAIN = { 1: "양호", 2: "보통", 3: "불량" };

const state = {
  defaults: null,
  scenario: null,
  result: null,
  key: "S0",
  view: "cultivator",
  basis: "total",
  rule: "shapley",
  selected: null,
  busy: false,
};

// ------------------------------------------------------------------ DOM 유틸
const $ = (id) => document.getElementById(id);

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  setAttrs(e, attrs);
  appendKids(e, kids);
  return e;
}
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  setAttrs(e, attrs);
  appendKids(e, kids);
  return e;
}
function setAttrs(e, attrs) {
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.setAttribute("class", v);
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : String(v));
  }
}
function appendKids(e, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}
function clear(e) {
  while (e.firstChild) e.removeChild(e.firstChild);
  return e;
}

// ------------------------------------------------------------------ 숫자 표기
const nf0 = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt = {
  int: (v) => nf0.format(Math.round(v)),
  one: (v) => nf1.format(v),
  two: (v) => nf2.format(v),
  mwon: (v) => nf1.format(v / 1e6),
  manwon: (v) => nf0.format(Math.round(v / 1e4)),
  signedManwon: (v) => `${v >= 0 ? "+" : "−"}${nf0.format(Math.abs(Math.round(v / 1e4)))}만원`,
  pct: (v) => `${v >= 0 ? "+" : "−"}${nf1.format(Math.abs(v * 100))}%`,
};

// ------------------------------------------------------------------ 색 토큰
function tok(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function pal() {
  return {
    s1: tok("--series-1"),
    s2: tok("--series-2"),
    s3: tok("--series-3"),
    pos: tok("--pos"),
    neg: tok("--neg"),
    deemph: tok("--deemph"),
    deemph2: tok("--deemph-2"),
    fixed: tok("--fixed"),
    mapbg: tok("--map-bg"),
    surface: tok("--surface-1"),
    text: tok("--text-primary"),
    text2: tok("--text-secondary"),
    muted: tok("--text-muted"),
    grid: tok("--grid"),
    axis: tok("--axis"),
  };
}

// ------------------------------------------------------------------ API
async function api(path, body) {
  const opt =
    body === undefined
      ? {}
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opt);
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    data = null;
  }
  if (!res.ok) {
    let detail = res.statusText;
    if (data && data.detail) {
      detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg || JSON.stringify(d)).join(" / ") : String(data.detail);
    }
    throw new Error(detail);
  }
  return data;
}

function setStatus(text) {
  $("status").textContent = text;
}
function showAlert(msg) {
  const a = $("alert");
  a.textContent = msg || "";
  a.hidden = !msg;
}
function setBusy(on, text) {
  state.busy = on;
  $("busy").hidden = !on;
  $("busyText").textContent = text || "계산 중…";
  for (const id of ["btnRun", "btnDemo", "btnExport"]) $(id).disabled = on;
  document.querySelector(".results").classList.toggle("stale", on);
}

// ------------------------------------------------------------------ 시나리오 조회 도우미
function features() {
  return state.scenario ? state.scenario.parcels.features : [];
}
function pidOf(f) {
  return String((f.properties && f.properties.id) || f.id);
}
let farmerById = new Map();
let resultIndex = new Map();

function isMember(owner) {
  const f = farmerById.get(owner);
  return f ? !!f.member : false;
}
function nameOf(cid) {
  if (cid === COOP) return "공동영농법인";
  const f = farmerById.get(cid);
  return f ? f.name : cid;
}
function currentScenario() {
  if (!state.result) return null;
  return state.result.scenarios.find((s) => s.key === state.key) || null;
}
function scenarioByKey(key) {
  return state.result ? state.result.scenarios.find((s) => s.key === key) : null;
}
function origAssign(props) {
  return isMember(props.owner) ? COOP : props.owner;
}
function cultivatorOf(f, sc = currentScenario()) {
  if (sc) {
    const i = resultIndex.get(pidOf(f));
    if (i !== undefined) return sc.assign[i];
  }
  return f.properties.owner;
}
function cropOf(f, sc = currentScenario()) {
  if (sc) {
    const i = resultIndex.get(pidOf(f));
    if (i !== undefined) return sc.crops[i];
  }
  return f.properties.current_crop;
}
/** 지금 보는 시나리오에서 '조합원(법인) 묶음'에 속하는 필지인가. 현황은 조합원 소유지, 그 외는 법인 경작지. */
function inGroup(f, sc = currentScenario()) {
  if (state.key === "S0" || !sc) return isMember(f.properties.owner);
  return cultivatorOf(f, sc) === COOP;
}

// ------------------------------------------------------------------ 지도
let map = null;
let parcelLayer = null;
let overlay = null;
let baseLayer = null;
const layerByPid = new Map();

function initMap() {
  map = L.map("map", { zoomSnap: 0.25, zoomControl: true });
  map.attributionControl.setPrefix(false);
  overlay = L.layerGroup().addTo(map);
  map.setView([36.097, 129.345], 15);
}

function toggleBasemap(on) {
  if (on && !baseLayer) {
    baseLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    });
  }
  if (on) baseLayer.addTo(map).bringToBack();
  else if (baseLayer) map.removeLayer(baseLayer);
}

function renderParcels(fit = true) {
  if (parcelLayer) map.removeLayer(parcelLayer);
  layerByPid.clear();
  parcelLayer = L.geoJSON(state.scenario.parcels, {
    style: (f) => styleFor(f),
    onEachFeature: (f, layer) => {
      layerByPid.set(pidOf(f), layer);
      layer.on("click", () => onParcelClick(f, layer));
    },
  }).addTo(map);
  if (fit) {
    const b = parcelLayer.getBounds();
    if (state.scenario.depot) b.extend(lonlatToLatLng(state.scenario.depot));
    for (const f of state.scenario.farmers || []) if (f.home) b.extend(lonlatToLatLng(f.home));
    map.fitBounds(b, { padding: [24, 24] });
  }
}

function selectedMatch(f, sc) {
  if (!state.selected) return false;
  const cult = cultivatorOf(f, sc);
  const selIsCoopMember = state.key !== "S0" && sc && isMember(state.selected);
  return selIsCoopMember ? f.properties.owner === state.selected : cult === state.selected;
}

function styleFor(f) {
  const P = pal();
  const sc = currentScenario();
  const base = { color: P.mapbg, weight: 1, opacity: 1, fillOpacity: 0.9 };
  const cult = cultivatorOf(f, sc);
  const crop = cropOf(f, sc);
  switch (state.view) {
    case "crop": {
      const w = WATER[crop] || "none";
      const fill = w === "wet" ? P.s1 : w === "dry" ? P.s2 : P.fixed;
      const dim = state.key !== "S0" && sc && cult !== COOP;
      return { ...base, fillColor: fill, fillOpacity: dim ? 0.28 : 0.9 };
    }
    case "exchange": {
      let fill = P.deemph2;
      if (sc && sc.exchange) {
        const from = origAssign(f.properties);
        if (from !== cult) fill = cult === COOP ? P.s1 : from === COOP ? P.s2 : P.s3;
      }
      const st = { ...base, fillColor: fill };
      if (f.properties.exchangeable === false) {
        st.color = P.fixed;
        st.weight = 2;
      }
      return st;
    }
    case "route": {
      const g = state.key !== "S0" && inGroup(f, sc);
      return { ...base, fillColor: g ? P.s1 : P.deemph, fillOpacity: g ? 0.25 : 0.6 };
    }
    default: {
      let fill = inGroup(f, sc) ? P.s1 : P.deemph;
      if (selectedMatch(f, sc)) fill = P.s2;
      return { ...base, fillColor: fill };
    }
  }
}

function restyle() {
  if (!parcelLayer) return;
  parcelLayer.eachLayer((layer) => layer.setStyle(styleFor(layer.feature)));
  renderOverlays();
  renderLegend();
}

function lonlatToLatLng(p) {
  return [p[1], p[0]];
}

function renderOverlays() {
  overlay.clearLayers();
  if (!state.scenario) return;
  const P = pal();
  const sc = currentScenario();
  const depot = state.scenario.depot;

  if (state.view === "crop" && sc && state.key !== "S0") {
    for (const wb of sc.work_blocks || []) {
      if (wb.cultivator !== COOP || wb.area_ha < 0.5 || !CROPS.includes(wb.crop)) continue;
      const span = h("span", { class: "maplabel", text: wb.crop });
      L.marker(lonlatToLatLng(wb.centroid), {
        icon: L.divIcon({ className: "", html: span, iconSize: null }),
        interactive: false,
        keyboard: false,
      }).addTo(overlay);
    }
  }

  if (state.view === "route" && sc) {
    const routes = [...(sc.routes || [])].sort((a, b) => (a.cultivator === COOP) - (b.cultivator === COOP));
    for (const r of routes) {
      const coop = r.cultivator === COOP;
      const line = L.polyline(r.path.map(lonlatToLatLng), {
        color: coop ? P.s1 : P.muted,
        weight: coop ? 2.5 : 1,
        opacity: coop ? 0.95 : 0.55,
        lineJoin: "round",
        lineCap: "round",
      }).addTo(overlay);
      line.bindTooltip(
        `${nameOf(r.cultivator)} · ${r.crop} 작업 동선 ${fmt.one(r.length_km)}km (블록 ${r.blocks}개)`,
        { sticky: true }
      );
    }
    for (const f of state.scenario.farmers || []) {
      if (!f.home) continue;
      L.circleMarker(lonlatToLatLng(f.home), {
        radius: 3,
        color: P.surface,
        weight: 1,
        fillColor: P.muted,
        fillOpacity: 1,
      })
        .bindTooltip(`${f.name}의 집`)
        .addTo(overlay);
    }
  }

  if (depot && state.view !== "exchange") {
    L.circleMarker(lonlatToLatLng(depot), {
      radius: 7,
      color: P.surface,
      weight: 2,
      fillColor: P.text,
      fillOpacity: 1,
    })
      .bindTooltip("공동영농 농기계 보관소(가정 위치)")
      .addTo(overlay);
  }
}

function legendItem(color, label, kind = "box") {
  const sw = h("span", { class: `sw${kind === "line" ? " line" : ""}${kind === "ring" ? " ring" : ""}` });
  if (kind !== "ring") sw.style.background = color;
  else sw.style.borderColor = color;
  return h("div", { class: "item" }, sw, h("span", { text: label }));
}

function renderLegend() {
  const box = clear($("legend"));
  if (!state.scenario) return;
  const P = pal();
  const sc = currentScenario();
  const s0 = state.key === "S0";
  if (state.view === "cultivator") {
    box.append(h("div", { class: "title", text: "경작자" }));
    box.append(legendItem(P.s1, s0 ? "조합원 소유 필지(지금은 따로 경작)" : "공동영농법인 경작"));
    box.append(legendItem(P.deemph, "비참여 농가 경작"));
    if (state.selected) {
      const which = !s0 && sc && isMember(state.selected) ? "소유 필지" : "경작 필지";
      box.append(legendItem(P.s2, `선택: ${nameOf(state.selected)} ${which}`));
    }
    box.append(h("div", { class: "note", text: "필지를 누르면 그 농가의 필지를 강조합니다." }));
  } else if (state.view === "crop") {
    box.append(h("div", { class: "title", text: "작목(물 관리 방식)" }));
    box.append(legendItem(P.s1, "담수 작목: 벼·가루쌀"));
    box.append(legendItem(P.s2, "밭 작목: 콩·조사료"));
    box.append(legendItem(P.fixed, "시설채소(고정)"));
    if (!s0 && sc) box.append(h("div", { class: "note", text: "흐린 필지는 비참여 농가 경작지입니다. 글자는 법인 작목 블록입니다." }));
  } else if (state.view === "exchange") {
    box.append(h("div", { class: "title", text: "경작권 교환" }));
    box.append(legendItem(P.s1, "법인으로 편입"));
    box.append(legendItem(P.s2, "법인에서 내줌"));
    box.append(legendItem(P.s3, "개별 농가 사이 교환"));
    box.append(legendItem(P.deemph2, "변동 없음"));
    box.append(legendItem(P.fixed, "교환 제외(고정) 필지", "ring"));
    if (!sc || !sc.exchange) box.append(h("div", { class: "note", text: "1:1 교환·다자간 교환 시나리오에서 보입니다." }));
  } else {
    box.append(h("div", { class: "title", text: "농기계 작업 동선" }));
    box.append(legendItem(P.s1, "법인 동선(작목별)", "line"));
    box.append(legendItem(P.muted, "개별 농가 동선", "line"));
    box.append(legendItem(P.text, "농기계 보관소"));
    box.append(
      h("div", { class: "note", text: sc ? "선은 블록 중심을 방문 순서대로 이은 것이며 실제 도로가 아닙니다." : "최적화를 실행하면 동선이 보입니다." })
    );
  }
}

function onParcelClick(f, layer) {
  const sc = currentScenario();
  const cult = cultivatorOf(f, sc);
  const owner = f.properties.owner;
  state.selected = cult === COOP ? owner : cult;
  restyle();
  layer.bindPopup(popupFor(f, sc), { maxWidth: 320 }).openPopup();
}

function popupFor(f, sc) {
  const p = f.properties;
  const area = layerArea(f);
  const cult = cultivatorOf(f, sc);
  const from = sc && state.key !== "S0" ? origAssign(p) : p.owner;
  const crop = cropOf(f, sc);
  const rows = [
    ["소유 농가", `${nameOf(p.owner)}${isMember(p.owner) ? " (조합원)" : ""}`],
    ["경작자", from !== cult ? `${nameOf(from)} → ${nameOf(cult)}` : nameOf(cult)],
    ["작목", crop !== p.current_crop ? `${p.current_crop} → ${crop}` : crop],
    ["토양 적성등급", `${p.soil_grade}등급`],
    ["배수", DRAIN[p.drainage] || "-"],
    ["관개", p.irrigated ? "가능" : "불가"],
    ["공시지가", `${fmt.int(p.land_value)}원/㎡`],
    ["교환", p.exchangeable === false ? "제외(고정)" : "가능"],
  ];
  return h(
    "div",
    { class: "popup" },
    h("h3", { text: `${pidOf(f)} · ${p.land_type} ${area ? fmt.two(area) + "ha" : ""}` }),
    h("table", {}, rows.map(([k, v]) => h("tr", {}, h("th", { scope: "row", text: k }), h("td", { text: v }))))
  );
}

function layerArea(f) {
  // 화면 표시용 근사 면적(ha). 서버 계산과 같은 등장방형 근사.
  const ring = f.geometry.type === "Polygon" ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
  if (!ring || ring.length < 3) return 0;
  const lat0 = ring[0][1];
  const kx = 111320 * Math.cos((lat0 * Math.PI) / 180);
  const ky = 110574;
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [x1, y1] = [ring[i][0] * kx, ring[i][1] * ky];
    const [x2, y2] = [ring[i + 1][0] * kx, ring[i + 1][1] * ky];
    a += x1 * y2 - x2 * y1;
  }
  return Math.abs(a) / 2 / 1e4;
}

// ------------------------------------------------------------------ 툴팁
const tip = () => $("tooltip");
function showTip(evt, value, label, color) {
  const t = clear(tip());
  t.append(h("div", { class: "v", text: value }));
  const k = h("div", { class: "k" });
  if (color) {
    const key = h("span", { class: "key" });
    key.style.background = color;
    k.append(key);
  }
  k.append(h("span", { text: label }));
  t.append(k);
  t.hidden = false;
  let x;
  let y;
  if (evt.type === "focus") {
    const r = evt.target.getBoundingClientRect();
    x = r.right;
    y = r.top;
  } else {
    x = evt.clientX;
    y = evt.clientY;
  }
  const w = t.offsetWidth;
  const left = x + 14 + w > window.innerWidth ? x - w - 14 : x + 14;
  t.style.left = `${Math.max(4, left)}px`;
  t.style.top = `${Math.max(4, y - 10)}px`;
}
function hideTip() {
  tip().hidden = true;
}
function hoverable(node, value, label, color) {
  node.setAttribute("tabindex", "0");
  node.addEventListener("pointermove", (e) => showTip(e, value, label, color));
  node.addEventListener("pointerleave", hideTip);
  node.addEventListener("focus", (e) => showTip(e, value, label, color));
  node.addEventListener("blur", hideTip);
}

// ------------------------------------------------------------------ 결과: 지표
const METRICS = [
  { key: "blocks", label: "경작 블록 수", short: "블록", unit: "개", better: "down", fmt: (v) => fmt.int(v) },
  { key: "machine_hours", label: "농기계 작업시간", short: "작업시간", unit: "시간", better: "down", fmt: (v) => fmt.int(v) },
  { key: "travel_km", label: "농기계 이동거리", short: "이동거리", unit: "km", better: "down", fmt: (v) => fmt.int(v) },
  { key: "net_income", label: "순이익", short: "순이익", unit: "백만원", better: "up", fmt: (v) => fmt.mwon(v) },
];

function metricSource(sc) {
  return state.basis === "group" ? sc.group : sc.totals;
}

function renderKPIs() {
  const box = clear($("kpis"));
  const flags = clear($("flags"));
  $("kpiTitle").textContent = state.result ? `핵심 지표 · ${SCEN.find((s) => s.key === state.key).short}` : "핵심 지표";
  const sc = currentScenario();
  if (!sc) {
    box.append(h("p", { class: "empty", text: "최적화를 실행하면 지표가 나타납니다." }));
    return;
  }
  const base = metricSource(scenarioByKey("S0"));
  const cur = metricSource(sc);
  for (const m of METRICS) {
    const v = cur[m.key];
    const v0 = base[m.key];
    const tile = h(
      "div",
      { class: "tile" },
      h("div", { class: "label", text: state.basis === "group" && m.key === "net_income" ? "조합원 필지 순이익" : m.label }),
      h("div", { class: "value" }, m.fmt(v), h("small", { text: m.unit }))
    );
    if (state.key !== "S0" && v0) {
      const d = (v - v0) / Math.abs(v0);
      const good = m.better === "down" ? d < 0 : d > 0;
      const icon = d === 0 ? "■" : d < 0 ? "▼" : "▲";
      tile.append(
        h("div", { class: `delta ${Math.abs(d) < 1e-9 ? "" : good ? "good" : "bad"}`, text: `${icon} 현황 대비 ${fmt.pct(d)}` })
      );
    } else {
      tile.append(h("div", { class: "delta", text: "기준 시나리오" }));
    }
    box.append(tile);
  }

  const g = sc.group;
  if (state.key !== "S0") {
    const rule = state.result.pilot_rule;
    const ok = sc.pilot_eligible;
    flags.append(
      h(
        "span",
        { class: "flag" },
        h("b", { class: ok ? "ok" : "ng", text: ok ? "✓ 충족" : "✕ 미충족" }),
        ` 시범사업 면적·인원 요건(${rule.min_area_ha}ha·${rule.min_members}명): 법인 ${fmt.one(g.area_ha)}ha · 조합원 ${state.result.members.length}명`
      )
    );
  }
  if (sc.exchange) {
    const st = sc.exchange.stats;
    flags.append(h("span", { class: "flag" }, "교환 필지 ", h("b", { text: `${st.changed_parcels}개` }), ` (${fmt.one(st.changed_area_ha)}ha)`));
    const nViol = (st.ir_violations || []).length;
    flags.append(
      h("span", { class: "flag" }, "교환 전보다 불리해진 농가 ", h("b", { class: nViol ? "ng" : "ok", text: `${nViol}명` }))
    );
  }
  flags.append(h("span", { class: "flag" }, "계산 ", h("b", { text: `${fmt.one(state.result.timings.total)}초` })));
  flags.append(h("button", { type: "button", class: "linkbtn", text: "결과 JSON 내려받기", onclick: downloadResult }));
}

// ------------------------------------------------------------------ 결과: 시나리오 비교(소형 다중 막대)
function barPath(x0, y, w, hgt, r) {
  if (w <= 0) return "";
  const rr = Math.min(r, w, hgt / 2);
  return `M${x0},${y}h${w - rr}a${rr},${rr} 0 0 1 ${rr},${rr}v${hgt - 2 * rr}a${rr},${rr} 0 0 1 ${-rr},${rr}h${-(w - rr)}z`;
}

function renderCompare() {
  const box = clear($("compare"));
  const tbl = clear($("compareTable"));
  if (!state.result) {
    box.append(h("p", { class: "empty", text: "최적화를 실행하면 네 시나리오를 나란히 비교합니다." }));
    return;
  }
  const P = pal();
  const labelW = 70;
  const valW = 44;
  const rowH = 21;
  const barH = 12;
  const fs = 11;
  for (const m of METRICS) {
    const holder = h("div", { class: "mchart" }, h("h3", { text: `${m.label}(${m.unit})` }));
    box.append(holder);
    const W = Math.max(200, Math.round(holder.clientWidth || 200));
    const vals = SCEN.map((s) => metricSource(scenarioByKey(s.key))[m.key]);
    const max = Math.max(...vals.map((v) => Math.abs(v)), 1e-9);
    const plotW = W - labelW - valW;
    const svg = sv("svg", { viewBox: `0 0 ${W} ${rowH * SCEN.length + 6}`, role: "img", "aria-label": `${m.label} 시나리오 비교` });
    svg.append(sv("line", { x1: labelW, x2: labelW, y1: 0, y2: rowH * SCEN.length + 2, stroke: P.axis, "stroke-width": 1 }));
    SCEN.forEach((s, k) => {
      const v = vals[k];
      const y = k * rowH + (rowH - barH) / 2 + 2;
      const w = (Math.max(v, 0) / max) * plotW;
      const sel = s.key === state.key;
      const color = sel ? P.s1 : P.deemph;
      svg.append(sv("text", { x: labelW - 6, y: y + barH - 2, "text-anchor": "end", "font-size": fs, "font-weight": sel ? 600 : 400, fill: sel ? P.text : P.text2, text: s.short }));
      const bar = sv("path", { d: barPath(labelW, y, w, barH, 4), fill: color, class: "bar" });
      hoverable(bar, `${m.fmt(v)} ${m.unit}`, `${s.long}`, color);
      svg.append(bar);
      svg.append(sv("text", { x: labelW + w + 4, y: y + barH - 2, "font-size": fs, fill: P.text2, text: m.fmt(v) }));
    });
    holder.append(svg);
  }
  // 표 보기
  const table = h(
    "table",
    { class: "data" },
    h("thead", {}, h("tr", {}, h("th", { text: "시나리오" }), METRICS.map((m) => h("th", { text: `${m.short}(${m.unit})` })))),
    h(
      "tbody",
      {},
      SCEN.map((s) => {
        const src = metricSource(scenarioByKey(s.key));
        return h("tr", {}, h("td", { text: s.short }), METRICS.map((m) => h("td", { text: m.fmt(src[m.key]) })));
      })
    )
  );
  tbl.append(h("div", { class: "tablewrap" }, table));
}

// ------------------------------------------------------------------ 결과: 작목 배치
let miniProj = null;
function buildMiniProjection() {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const feats = features();
  const lat0 = feats.length ? feats[0].geometry.coordinates[0][0][1] : 36;
  const kx = Math.cos((lat0 * Math.PI) / 180);
  for (const f of feats) {
    const ring = f.geometry.type === "Polygon" ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
    for (const [lon, lat] of ring) {
      const x = lon * kx;
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, lat);
      maxY = Math.max(maxY, lat);
    }
  }
  const W = 200;
  const scale = W / (maxX - minX || 1);
  const H = (maxY - minY) * scale;
  miniProj = {
    W,
    H,
    path(f) {
      const ring = f.geometry.type === "Polygon" ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
      return (
        ring
          .map(([lon, lat], i) => `${i ? "L" : "M"}${((lon * kx - minX) * scale).toFixed(1)},${((maxY - lat) * scale).toFixed(1)}`)
          .join("") + "Z"
      );
    },
  };
}

function renderZoning() {
  const box = clear($("zoning"));
  const sc = currentScenario();
  if (!state.scenario) return;
  const P = pal();
  const feats = features();
  const group = feats.filter((f) => inGroup(f, sc));
  const groupArea = group.reduce((a, f) => a + layerArea(f), 0);

  if (sc && sc.zoning) {
    const z = sc.zoning;
    box.append(
      h(
        "p",
        { class: "summary" },
        "혼합정수계획(HiGHS) ",
        h("b", { text: z.status }),
        ` · 이진변수 ${fmt.int(z.n_binary)}개 · ${fmt.two(z.seconds)}초`,
        z.relaxed ? " · 작목 비율 조건을 동시에 맞출 수 없어 비율 제약 없이 배치했습니다." : ""
      )
    );
  } else {
    box.append(h("p", { class: "summary", text: state.key === "S0" ? "현황: 조합원 필지의 지금 작목 구성입니다. 공동영농 시나리오를 고르면 법인 작목 배치안이 보입니다." : "최적화를 실행하면 법인 작목 배치안이 보입니다." }));
  }

  // 작목별 면적·블록 표
  const blocksByCrop = {};
  if (sc && state.key !== "S0") {
    for (const wb of sc.work_blocks || []) if (wb.cultivator === COOP) blocksByCrop[wb.crop] = (blocksByCrop[wb.crop] || 0) + 1;
  }
  const bounds = sc && sc.zoning ? sc.zoning.bounds_used || {} : {};
  const areaBy = {};
  for (const f of group) {
    const c = cropOf(f, sc);
    areaBy[c] = (areaBy[c] || 0) + layerArea(f);
  }
  const crops = [...CROPS, ...Object.keys(areaBy).filter((c) => !CROPS.includes(c))];
  const rows = crops
    .filter((c) => areaBy[c])
    .map((c) =>
      h(
        "tr",
        {},
        h("td", { text: c }),
        h("td", { text: fmt.one(areaBy[c]) }),
        h("td", { text: groupArea ? `${fmt.one((areaBy[c] / groupArea) * 100)}%` : "-" }),
        h("td", { text: state.key === "S0" ? "-" : String(blocksByCrop[c] || 0) }),
        h("td", { text: bounds[c] ? `${Math.round(bounds[c][0] * 100)}~${Math.round(bounds[c][1] * 100)}%` : "-" })
      )
    );
  box.append(
    h(
      "div",
      { class: "tablewrap" },
      h(
        "table",
        { class: "data" },
        h("thead", {}, h("tr", {}, ["작목", "면적(ha)", "비율", "작업 블록", "허용 범위"].map((t) => h("th", { text: t })))),
        h("tbody", {}, rows)
      )
    )
  );
  if (sc && sc.zoning && sc.zoning.breakdown) {
    const b = sc.zoning.breakdown;
    box.append(
      h(
        "dl",
        { class: "kv" },
        h("dt", { text: "작물 소득 + 전략작물직불금" }),
        h("dd", { text: `${fmt.mwon(b.revenue)}백만원` }),
        h("dt", { text: "단지화 보상(목적함수 항)" }),
        h("dd", { text: `${fmt.mwon(b.contiguity_bonus)}백만원` }),
        h("dt", { text: "습해 위험 벌점(목적함수 항)" }),
        h("dd", { text: `${fmt.mwon(b.seepage_penalty)}백만원` })
      )
    );
  }

  // 작목별 작은 지도(강조형): 각 작목 필지만 강조색, 나머지 묶음 필지는 회색
  if (!miniProj) buildMiniProjection();
  const grid = h("div", { class: "minimaps" });
  for (const c of CROPS) {
    const svg = sv("svg", { viewBox: `0 0 ${miniProj.W} ${miniProj.H.toFixed(1)}`, role: "img", "aria-label": `${c} 배치 지도` });
    for (const f of feats) {
      const g = inGroup(f, sc);
      const hit = g && cropOf(f, sc) === c;
      svg.append(sv("path", { d: miniProj.path(f), fill: hit ? P.s1 : g ? P.deemph : P.deemph2, stroke: P.surface, "stroke-width": 0.6 }));
    }
    grid.append(
      h(
        "div",
        { class: "minimap" },
        h(
          "div",
          { class: "cap" },
          h("b", { text: c }),
          h("span", { text: `${fmt.one(areaBy[c] || 0)}ha${state.key !== "S0" && sc ? ` · 블록 ${blocksByCrop[c] || 0}` : ""}` })
        ),
        svg
      )
    );
  }
  box.append(grid);
}

// ------------------------------------------------------------------ 결과: 교환 내역
function renderExchange() {
  const box = clear($("exchange"));
  if (!state.result) {
    box.append(h("p", { class: "empty", text: "최적화를 실행하면 교환안이 보입니다." }));
    return;
  }
  const s2 = scenarioByKey("S2");
  const s3 = scenarioByKey("S3");
  const cmp = h(
    "table",
    { class: "data" },
    h("thead", {}, h("tr", {}, ["방식", "교환 필지", "교환 면적(ha)", "전체 블록", "불리해진 농가"].map((t) => h("th", { text: t })))),
    h(
      "tbody",
      {},
      [s2, s3].map((s) =>
        h(
          "tr",
          {},
          h("td", { text: SCEN.find((x) => x.key === s.key).short }),
          h("td", { text: fmt.int(s.exchange.stats.changed_parcels) }),
          h("td", { text: fmt.one(s.exchange.stats.changed_area_ha) }),
          h("td", { text: fmt.int(s.totals.blocks) }),
          h("td", { text: `${(s.exchange.stats.ir_violations || []).length}명` })
        )
      )
    )
  );
  box.append(h("div", { class: "tablewrap" }, cmp));

  const sc = currentScenario();
  if (!sc || !sc.exchange) {
    box.append(h("p", { class: "summary", text: "1:1 교환 또는 다자간 교환 시나리오를 고르면 경작자별 내역이 보입니다." }));
    return;
  }
  const ex = sc.exchange;
  const involved = ex.cultivators.filter((c) => c.given + c.received > 0);
  involved.sort((a, b) => (b.id === COOP) - (a.id === COOP) || b.given + b.received - (a.given + a.received));
  box.append(
    h(
      "p",
      { class: "summary" },
      `${SCEN.find((x) => x.key === sc.key).short}: 교환에 참여한 경작자 `,
      h("b", { text: `${involved.length}명` }),
      ` · 평가가치는 공시지가×면적×토양계수입니다. 허용오차 ±${Math.round(state.result.settings.value_tol * 100)}%.`
    )
  );
  const rows = involved.map((c) =>
    h(
      "tr",
      { class: c.id === COOP ? "coop" : "" },
      h("td", { text: nameOf(c.id) }),
      h("td", { text: `${fmt.two(c.area0_ha)}→${fmt.two(c.area_ha)}` }),
      h("td", { text: `${c.blocks0}→${c.blocks}` }),
      h("td", { text: fmt.pct((c.value - c.value0) / c.value0) }),
      h("td", { text: `${c.received}/${c.given}` }),
      h("td", { class: c.ir_ok ? "ok" : "ng", text: c.id === COOP ? "-" : c.ir_ok ? "✓ 유지·개선" : "✕ 불리" })
    )
  );
  box.append(
    h(
      "div",
      { class: "tablewrap" },
      h(
        "table",
        { class: "data" },
        h("thead", {}, h("tr", {}, ["경작자", "면적(ha)", "블록", "평가가치", "받음/내줌", "분산 점수"].map((t) => h("th", { text: t })))),
        h("tbody", {}, rows)
      )
    )
  );
}

// ------------------------------------------------------------------ 결과: 수익 배분
function renderRuleSeg() {
  const seg = clear($("ruleSeg"));
  const sh = state.result && state.result.sharing;
  if (!sh) return;
  for (const [key, r] of Object.entries(sh.rules)) {
    seg.append(
      h("button", {
        type: "button",
        "aria-pressed": String(key === state.rule),
        text: r.label,
        onclick: () => {
          state.rule = key;
          renderRuleSeg();
          renderSharing();
        },
      })
    );
  }
}

function renderSharing() {
  const box = clear($("sharing"));
  const tbl = clear($("shareTable"));
  const sh = state.result && state.result.sharing;
  if (!sh) {
    box.append(h("p", { class: "empty", text: state.result ? "조합원이 없어 배분을 계산하지 않았습니다." : "최적화를 실행하면 배분안이 보입니다." }));
    return;
  }
  const P = pal();
  const rule = sh.rules[state.rule];
  const n = sh.members.length;
  box.append(
    h(
      "p",
      { class: "summary" },
      "개별영농보다 나아지는 조합원 ",
      h("b", { text: `${rule.better_off}/${n}명` }),
      " · 평당 배분 ",
      h("b", { text: `${fmt.int(sh.per_pyeong)}원` }),
      " · 협동잉여 ",
      h("b", { text: `${fmt.mwon(sh.surplus)}백만원` })
    )
  );
  const rows = [...sh.members].sort((a, b) => b.gain[state.rule] - a.gain[state.rule]);
  const chartBox = h("div", { class: "divchart" });
  box.append(chartBox);
  const W = Math.max(320, Math.round(chartBox.clientWidth || 400));
  const labelW = 58;
  const valW = 70;
  const rowH = 19;
  const barH = 12;
  const fs = 11;
  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.gain[state.rule])), 1);
  const plotW = W - labelW - valW * 2;
  const zeroX = labelW + valW + plotW / 2;
  const half = plotW / 2;
  const H = rows.length * rowH + 24;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${rule.label} 기준 조합원별 개별영농 대비 증감` });
  svg.append(sv("text", { x: zeroX - 4, y: 10, "text-anchor": "end", "font-size": fs, fill: P.muted, text: "◀ 손해" }));
  svg.append(sv("text", { x: zeroX + 4, y: 10, "font-size": fs, fill: P.muted, text: "이득 ▶" }));
  rows.forEach((r, k) => {
    const g = r.gain[state.rule];
    const y = 18 + k * rowH + (rowH - barH) / 2;
    const w = (Math.abs(g) / maxAbs) * half;
    const color = g >= 0 ? P.pos : P.neg;
    svg.append(sv("text", { x: 0, y: y + barH - 2, "font-size": fs, fill: P.text2, text: r.name }));
    let d;
    if (g >= 0) d = barPath(zeroX, y, w, barH, 4);
    else {
      // 왼쪽으로 자라는 막대: 둥근 끝이 왼쪽
      const rr = Math.min(4, w, barH / 2);
      d = w > 0 ? `M${zeroX},${y}h${-(w - rr)}a${rr},${rr} 0 0 0 ${-rr},${rr}v${barH - 2 * rr}a${rr},${rr} 0 0 0 ${rr},${rr}h${w - rr}z` : "";
    }
    const bar = sv("path", { d, fill: color, class: "bar" });
    hoverable(
      bar,
      fmt.signedManwon(g),
      `${r.name}: 개별영농 ${fmt.manwon(r.individual)}만원 → 배분 ${fmt.manwon(r.shares[state.rule])}만원`,
      color
    );
    svg.append(bar);
    const tx = g >= 0 ? zeroX + w + 4 : zeroX - w - 4;
    svg.append(sv("text", { x: tx, y: y + barH - 2, "font-size": fs, "text-anchor": g >= 0 ? "start" : "end", fill: P.text2, text: fmt.signedManwon(g) }));
  });
  svg.append(sv("line", { x1: zeroX, x2: zeroX, y1: 14, y2: H - 4, stroke: P.axis, "stroke-width": 1 }));
  chartBox.append(svg);
  box.append(
    h("p", {
      class: "hint",
      text: "개별영농 소득은 현황 시나리오에서 각 조합원이 자기 필지를 따로 지을 때의 순이익입니다. 배분액은 다자간 교환안의 법인 순이익을 고른 기준대로 나눈 값입니다.",
    })
  );

  const table = h(
    "table",
    { class: "data" },
    h(
      "thead",
      {},
      h("tr", {}, ["조합원", "나이", "면적", "노동일", "개별영농", "배분", "차이"].map((t) => h("th", { text: t })))
    ),
    h(
      "tbody",
      {},
      rows.map((r) =>
        h(
          "tr",
          {},
          h("td", { text: r.name }),
          h("td", { text: String(r.age) }),
          h("td", { text: fmt.two(r.area_ha) }),
          h("td", { text: fmt.int(r.labor_days) }),
          h("td", { text: fmt.manwon(r.individual) }),
          h("td", { text: fmt.manwon(r.shares[state.rule]) }),
          h("td", { class: r.gain[state.rule] >= 0 ? "ok" : "ng", text: fmt.signedManwon(r.gain[state.rule]).replace("만원", "") })
        )
      )
    )
  );
  tbl.append(h("p", { class: "hint", text: `면적은 ha, 금액은 만원. 배분 기준: ${rule.label}.` }));
  tbl.append(h("div", { class: "tablewrap" }, table));
}

// ------------------------------------------------------------------ 세그먼트·전체 렌더
function renderScenarioSeg() {
  const seg = clear($("scenarioSeg"));
  for (const s of SCEN) {
    seg.append(
      h("button", {
        type: "button",
        "aria-pressed": String(s.key === state.key),
        disabled: !state.result && s.key !== "S0",
        title: s.long,
        text: s.key === "S3" ? `${s.short}(제안)` : s.short,
        onclick: () => {
          state.key = s.key;
          renderAll();
        },
      })
    );
  }
}

function renderAll() {
  renderScenarioSeg();
  restyle();
  renderKPIs();
  renderCompare();
  renderZoning();
  renderExchange();
  renderRuleSeg();
  renderSharing();
  const meta = state.scenario ? state.scenario.meta || {} : {};
  $("mapNote").textContent = meta.synthetic ? "가상 데이터 · 실제 지형·소유와 무관" : "";
}

function bindSeg(id, attr, onPick) {
  $(id).addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn || !btn.dataset[attr]) return;
    for (const b of $(id).querySelectorAll("button")) b.setAttribute("aria-pressed", String(b === btn));
    onPick(btn.dataset[attr]);
  });
}

// ------------------------------------------------------------------ 입력·동작
function buildBoundsTable() {
  const body = clear($("boundsTable").querySelector("tbody"));
  const bounds = state.defaults.settings.crop_bounds;
  for (const c of state.defaults.crop_order) {
    const [lo, hi] = bounds[c] || [0, 1];
    body.append(
      h(
        "tr",
        {},
        h("td", { text: c }),
        h("td", {}, h("input", { type: "number", min: 0, max: 100, step: 5, value: Math.round(lo * 100), "data-crop": c, "data-side": "lo", "aria-label": `${c} 하한(%)` })),
        h("td", {}, h("input", { type: "number", min: 0, max: 100, step: 5, value: Math.round(hi * 100), "data-crop": c, "data-side": "hi", "aria-label": `${c} 상한(%)` }))
      )
    );
  }
}

function num(id, fallback) {
  const v = Number($(id).value);
  return Number.isFinite(v) ? v : fallback;
}

function readSettings() {
  const bounds = {};
  for (const input of $("boundsTable").querySelectorAll("input")) {
    const c = input.dataset.crop;
    bounds[c] = bounds[c] || [0, 1];
    bounds[c][input.dataset.side === "lo" ? 0 : 1] = Math.min(1, Math.max(0, Number(input.value) / 100));
  }
  for (const [c, [lo, hi]] of Object.entries(bounds)) {
    if (lo > hi) throw new Error(`${c}: 하한이 상한보다 큽니다.`);
  }
  return {
    area_tol: num("stAreaTol", 10) / 100,
    value_tol: num("stValueTol", 10) / 100,
    crop_bounds: bounds,
    merge_bunds: $("stMerge").checked,
    contiguity_weight: num("stContig", 1000),
    seepage_penalty: num("stSeep", 1500),
    w_change: num("stChange", 0.15),
    iterations: Math.round(num("stIter", 40000)),
    labor_share: num("stLabor", 20) / 100,
    shapley_samples: Math.round(num("stShapley", 150)),
  };
}

function setScenario(scn) {
  state.scenario = scn;
  state.result = null;
  state.key = "S0";
  state.selected = null;
  farmerById = new Map((scn.farmers || []).map((f) => [f.id, f]));
  resultIndex = new Map();
  miniProj = null;
  renderParcels(true);
  renderAll();
  const m = scn.meta || {};
  setStatus(
    `${m.name || "들녘"} · 필지 ${fmt.int(m.n_parcels || features().length)}개 · ${fmt.one(m.total_area_ha || 0)}ha · 조합원 ${m.n_members ?? "-"}명(${fmt.one(m.member_area_ha || 0)}ha)`
  );
  const warns = (m.warnings || []).join(" ");
  showAlert(warns ? `확인 필요: ${warns}` : "");
}

async function generateDemo() {
  showAlert("");
  setBusy(true, "가상 들녘을 만드는 중…");
  try {
    const body = {
      seed: Math.round(num("dmSeed", 7)),
      n_farmers: Math.round(num("dmFarmers", 30)),
      participation: num("dmParticipation", 60) / 100,
      fragmentation: num("dmFrag", 0.75),
    };
    setScenario(await api("/api/scenario/demo", body));
    return true;
  } catch (err) {
    showAlert(`가상 들녘을 만들지 못했습니다: ${err.message}`);
    return false;
  } finally {
    setBusy(false);
  }
}

async function runOptimize() {
  if (!state.scenario) return;
  showAlert("");
  let settings;
  try {
    settings = readSettings();
  } catch (err) {
    showAlert(err.message);
    return;
  }
  setBusy(true, "교환·작목 배치·수익 배분을 계산하는 중…");
  try {
    const res = await api("/api/optimize", { scenario: state.scenario, settings });
    state.result = res;
    resultIndex = new Map(res.parcel_ids.map((id, i) => [String(id), i]));
    state.key = "S3";
    state.selected = null;
    if (!res.sharing || !res.sharing.rules[state.rule]) state.rule = "shapley";
    renderAll();
    if ((res.warnings || []).length) showAlert(`확인 필요: ${res.warnings.join(" ")}`);
  } catch (err) {
    showAlert(`최적화에 실패했습니다: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

async function importFile(file) {
  showAlert("");
  setBusy(true, "파일을 읽고 인접 관계를 계산하는 중…");
  try {
    const text = await file.text();
    const json = JSON.parse(text);
    setScenario(await api("/api/scenario/import", json));
  } catch (err) {
    showAlert(`파일을 불러오지 못했습니다: ${err.message}`);
  } finally {
    setBusy(false);
    $("fileInput").value = "";
  }
}

function download(name, obj) {
  const blob = new Blob([JSON.stringify(obj, null, 1)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function downloadResult() {
  if (state.result) download("optimization-result.json", state.result);
}

function toggleTable(btnId, boxId) {
  $(btnId).addEventListener("click", () => {
    const box = $(boxId);
    box.hidden = !box.hidden;
    $(btnId).setAttribute("aria-expanded", String(!box.hidden));
    $(btnId).textContent = box.hidden ? "표로 보기" : "표 닫기";
  });
}

async function boot() {
  const theme = new URLSearchParams(window.location.search).get("theme");
  if (theme === "dark" || theme === "light") document.documentElement.dataset.theme = theme;
  initMap();
  bindSeg("viewSeg", "view", (v) => {
    state.view = v;
    restyle();
  });
  bindSeg("basisSeg", "basis", (v) => {
    state.basis = v;
    renderKPIs();
    renderCompare();
  });
  toggleTable("toggleCompareTable", "compareTable");
  toggleTable("toggleShareTable", "shareTable");
  $("btnDemo").addEventListener("click", generateDemo);
  $("btnRun").addEventListener("click", runOptimize);
  $("btnExport").addEventListener("click", () => state.scenario && download("field-scenario.json", state.scenario));
  $("fileInput").addEventListener("change", (e) => e.target.files[0] && importFile(e.target.files[0]));
  $("chkBasemap").addEventListener("change", (e) => toggleBasemap(e.target.checked));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderAll);
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      renderCompare();
      renderSharing();
    }, 150);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      state.selected = null;
      restyle();
    }
  });

  try {
    state.defaults = await api("/api/defaults");
  } catch (err) {
    showAlert(`서버에 연결하지 못했습니다: ${err.message}. 'python -m server'로 서버를 켰는지 확인하세요.`);
    setStatus("서버 연결 실패");
    return;
  }
  buildBoundsTable();
  renderAll();
  const ok = await generateDemo();
  const params = new URLSearchParams(window.location.search);
  if (ok && !params.has("noauto")) await runOptimize();
  applyUrlState(params);
}

/** 시연용: ?scenario=S0~S3&view=cultivator|crop|exchange|route&basis=total|group&rule=area|productivity|mixed|shapley */
function applyUrlState(params) {
  const key = params.get("scenario");
  if (key && SCEN.some((s) => s.key === key) && (state.result || key === "S0")) state.key = key;
  const view = params.get("view");
  if (view && ["cultivator", "crop", "exchange", "route"].includes(view)) {
    state.view = view;
    for (const b of $("viewSeg").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.view === view));
  }
  const basis = params.get("basis");
  if (basis && ["total", "group"].includes(basis)) {
    state.basis = basis;
    for (const b of $("basisSeg").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.basis === basis));
  }
  const rule = params.get("rule");
  if (rule && state.result && state.result.sharing && state.result.sharing.rules[rule]) state.rule = rule;
  if (params.has("tables")) {
    for (const [btn, box] of [["toggleCompareTable", "compareTable"], ["toggleShareTable", "shareTable"]]) {
      $(box).hidden = false;
      $(btn).setAttribute("aria-expanded", "true");
      $(btn).textContent = "표 닫기";
    }
  }
  renderAll();
}

boot();
