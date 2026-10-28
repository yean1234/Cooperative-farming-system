"use strict";

/*
 * 공동영농 후보단지 시뮬레이터 화면
 * API: GET /api/sites/regions, POST /api/sites/region, /api/sites/demo, /api/sites/import, /api/sites/solve, /api/sites/validate
 */

const CONSTRAINTS = [
  { key: "a_min_ha", label: "최소 단지 면적(C2)", unit: "ha", min: 5, max: 60, step: 1, value: 20 },
  { key: "a_max_ha", label: "최대 단지 면적(C2)", unit: "ha", min: 20, max: 150, step: 5, value: 60 },
  { key: "n_min", label: "최소 참여 농가 수(C3)", unit: "명", min: 1, max: 60, step: 1, value: 5 },
  { key: "slope_max", label: "경사 상한(C6)", unit: "%", min: 1, max: 15, step: 0.5, value: 7 },
  { key: "compact_min", label: "형상지수 하한(C7)", unit: "", min: 0, max: 0.5, step: 0.01, value: 0.2 },
  { key: "homog_min", label: "배수 동질성 하한(C8)", unit: "", min: 0.3, max: 1, step: 0.05, value: 0.6 },
];
const WEIGHTS = [
  { key: "w_income", label: "w1 예상 소득 증분", min: 0, max: 3, step: 0.1, value: 1 },
  { key: "w_mech", label: "w2 기계화 적합도", min: 0, max: 3, step: 0.1, value: 1 },
  { key: "w_consol", label: "w3 집단화 개선도", min: 0, max: 3, step: 0.1, value: 1 },
  { key: "w_friction", label: "w4 조성 마찰(감점)", min: 0, max: 3, step: 0.1, value: 1 },
];
const DRAIN = { 1: "양호", 2: "보통", 3: "불량" };

const state = { region: null, result: null, selected: null, busy: false, reqSeq: 0 };

// ------------------------------------------------------------------ 유틸
const $ = (id) => document.getElementById(id);
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  setAttrs(e, attrs);
  for (const k of kids.flat(Infinity)) if (k !== null && k !== undefined && k !== false) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  setAttrs(e, attrs);
  for (const k of kids.flat(Infinity)) if (k !== null && k !== undefined) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
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
function clear(e) {
  while (e.firstChild) e.removeChild(e.firstChild);
  return e;
}
const nf0 = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const tok = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function pal() {
  return {
    s1: tok("--series-1"), s2: tok("--series-2"), s3: tok("--series-3"),
    deemph: tok("--deemph"), deemph2: tok("--deemph-2"), mapbg: tok("--map-bg"),
    surface: tok("--surface-1"), text: tok("--text-primary"), text2: tok("--text-secondary"),
    muted: tok("--text-muted"), axis: tok("--axis"),
  };
}
async function api(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  if (!res.ok) {
    let d = res.statusText;
    if (data && data.detail) d = Array.isArray(data.detail) ? data.detail.map((x) => x.msg || JSON.stringify(x)).join(" / ") : String(data.detail);
    throw new Error(d);
  }
  return data;
}
async function apiGet(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}
const setStatus = (t) => ($("status").textContent = t);
function showAlert(msg) {
  $("alert").textContent = msg || "";
  $("alert").hidden = !msg;
}
function setBusy(on, text) {
  state.busy = on;
  $("busy").hidden = !on;
  $("busyText").textContent = text || "계산 중…";
  document.querySelector(".results").classList.toggle("stale", on);
}
const pid = (f) => String((f.properties && f.properties.id) || f.id);

// ------------------------------------------------------------------ 조건 입력
function buildSliders(boxId, defs) {
  const box = clear($(boxId));
  for (const d of defs) {
    const out = h("output", { id: `${d.key}Out`, text: fmtSlider(d, d.value) });
    const input = h("input", { type: "range", id: d.key, min: d.min, max: d.max, step: d.step, value: d.value, "aria-label": d.label });
    input.addEventListener("input", () => {
      out.textContent = fmtSlider(d, Number(input.value));
      scheduleSolve();
    });
    box.append(h("div", { class: "slider" }, h("div", { class: "sl-head" }, h("span", { text: d.label }), out), input));
  }
}
function fmtSlider(d, v) {
  if (d.step < 0.1) return nf2.format(v);
  if (d.step < 1) return `${nf1.format(v)}${d.unit || ""}`;
  return `${nf0.format(v)}${d.unit || ""}`;
}
function readSettings() {
  const s = {};
  for (const d of [...CONSTRAINTS, ...WEIGHTS]) s[d.key] = Number($(d.key).value);
  s.n_min = Math.round(s.n_min);
  if (s.a_max_ha < s.a_min_ha) {
    s.a_max_ha = s.a_min_ha;
  }
  s.require_road = $("requireRoad").checked;
  s.include_upland = $("includeUpland").checked;
  s.owner_mode = document.querySelector('input[name="ownerMode"]:checked').value;
  return s;
}
let solveTimer = null;
function scheduleSolve() {
  clearTimeout(solveTimer);
  solveTimer = setTimeout(runSolve, 250);
}

// ------------------------------------------------------------------ 지도
let map, parcelLayer, overlay, roadLayer, baseLayer;
const layerByPid = new Map();
let idx = new Map();

function initMap() {
  map = L.map("map", { zoomSnap: 0.25 });
  map.attributionControl.setPrefix(false);
  roadLayer = L.layerGroup().addTo(map);
  overlay = L.layerGroup().addTo(map);
  map.setView([36.067, 129.313], 14);
}
function renderParcels() {
  if (parcelLayer) map.removeLayer(parcelLayer);
  layerByPid.clear();
  idx = new Map(state.region.parcels.features.map((f, i) => [pid(f), i]));
  parcelLayer = L.geoJSON(state.region.parcels, {
    style: styleFor,
    onEachFeature: (f, layer) => {
      layerByPid.set(pid(f), layer);
      layer.on("click", () => onParcelClick(f, layer));
    },
  }).addTo(map);
  map.fitBounds(parcelLayer.getBounds(), { padding: [16, 16] });
  renderRoads();
}
function renderRoads() {
  roadLayer.clearLayers();
  if (!$("showRoads").checked) return;
  const P = pal();
  for (const line of (state.region.meta && state.region.meta.roads) || []) {
    L.polyline(line.map((p) => [p[1], p[0]]), { color: P.muted, weight: 1.5, opacity: 0.7, interactive: false }).addTo(roadLayer);
  }
}
function styleFor(f) {
  const P = pal();
  const i = idx.get(pid(f));
  const r = state.result;
  const st = { color: P.mapbg, weight: 0.8, opacity: 1, fillOpacity: 0.9 };
  if (r) {
    const k = r.assign[i];
    const status = r.status[i];
    if (k) st.fillColor = k === state.selected ? P.s2 : P.s1;
    else if (status === "eligible") st.fillColor = P.deemph2;
    else {
      st.fillColor = P.deemph;
      st.fillOpacity = 0.45;
    }
  } else st.fillColor = P.deemph2;
  if ($("showTruth").checked && f.properties.truth_complex > 0) {
    st.color = P.s3;
    st.weight = 2;
  }
  return st;
}
function restyle() {
  if (!parcelLayer) return;
  parcelLayer.eachLayer((l) => l.setStyle(styleFor(l.feature)));
  renderOverlay();
  renderLegend();
}
function renderOverlay() {
  overlay.clearLayers();
  const P = pal();
  for (const v of (state.region && state.region.meta.villages) || []) {
    L.circleMarker([v[1], v[0]], { radius: 5, color: P.surface, weight: 2, fillColor: P.text, fillOpacity: 1 }).bindTooltip("마을").addTo(overlay);
  }
  if (!state.result) return;
  for (const c of state.result.complexes) {
    const span = h("span", { class: "maplabel", text: `단지 ${c.id} · ${nf1.format(c.area_ha)}ha` });
    L.marker([c.centroid[1], c.centroid[0]], { icon: L.divIcon({ className: "", html: span, iconSize: null }), interactive: false, keyboard: false }).addTo(overlay);
  }
}
function legendItem(color, label, ring) {
  const sw = h("span", { class: `sw${ring ? " ring" : ""}` });
  if (ring) sw.style.borderColor = color;
  else sw.style.background = color;
  return h("div", { class: "item" }, sw, h("span", { text: label }));
}
function renderLegend() {
  const box = clear($("legend"));
  const P = pal();
  box.append(h("div", { class: "title", text: "후보 단지" }));
  box.append(legendItem(P.s1, "제안된 후보 단지"));
  if (state.selected) box.append(legendItem(P.s2, `선택한 단지 ${state.selected}`));
  box.append(legendItem(P.deemph2, "조건은 맞지만 단지에 안 들어간 필지"));
  box.append(legendItem(P.deemph, "제외 필지(경사·과수·시설 등)"));
  if ($("showTruth").checked) box.append(legendItem(P.s3, "정답 단지 경계(가상)", true));
  box.append(h("div", { class: "note", text: "필지를 누르면 포함·제외 근거를 보여 줍니다." }));
}
function toggleBasemap(on) {
  if (on && !baseLayer) baseLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" });
  if (on) baseLayer.addTo(map).bringToBack();
  else if (baseLayer) map.removeLayer(baseLayer);
}
function onParcelClick(f, layer) {
  const p = f.properties;
  const i = idx.get(pid(f));
  const r = state.result;
  const k = r ? r.assign[i] : 0;
  if (k) selectComplex(k, false);
  const synthetic = state.region.meta.synthetic;
  const src = (s) => (s ? ` · ${s}` : "");
  const rows = [
    ["지목·이용", `${p.jimok || p.land_type || "-"} · ${p.land_use || "-"}`],
    ["면적", `${nf2.format(areaHa(f))}ha`],
    ["경사", p.slope !== undefined ? `${p.slope_class || `${nf1.format(p.slope)}%`}${src(p.slope_source)}` : "-"],
    ["배수 등급", p.drainage !== undefined ? `${p.drainage_class || DRAIN[p.drainage]}${src(p.drainage_source)}` : synthetic ? "-" : "미확보(보통으로 계산)"],
    ["토양 적성", p.soil_grade ? `${p.suitability_grade || p.soil_grade}등급${src(p.soil_source)}` : synthetic ? "-" : "미확보"],
    ["도로 접속", p.road_access ? `있음${p.road_source ? ` · ${p.road_source}` : ""}` : "없음"],
    ["소유 농가", p.owner ? `${p.owner}${synthetic ? "(가상)" : ""}` : "비공개(필지 수로 추정)"],
  ];
  if (p.consolidated !== undefined) rows.splice(2, 0, ["경지정리", p.consolidated ? "됨" : "안 됨"]);
  if (!synthetic && p.land_value) rows.push(["공시지가", `${nf0.format(p.land_value)}원/㎡`]);
  if (p.pnu) rows.push(["PNU", p.pnu]);
  const box = h(
    "div",
    { class: "popup" },
    h("h3", { text: `${pid(f)} · ${k ? `단지 ${k} 포함` : r && r.status[i] === "eligible" ? "단지 밖(적격)" : "제외"}` }),
    r ? h("p", { class: "why", text: r.reasons[i] }) : null,
    h("table", {}, rows.map(([a, b]) => h("tr", {}, h("th", { text: a }), h("td", { text: b }))))
  );
  layer.bindPopup(box, { maxWidth: 320 }).openPopup();
}
function areaHa(f) {
  const ring = f.geometry.type === "Polygon" ? f.geometry.coordinates[0] : f.geometry.coordinates[0][0];
  const kx = 111320 * Math.cos((ring[0][1] * Math.PI) / 180);
  let a = 0;
  for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * kx * ring[i + 1][1] * 110574 - ring[i + 1][0] * kx * ring[i][1] * 110574;
  return Math.abs(a) / 2 / 1e4;
}

// ------------------------------------------------------------------ 결과 패널
function tile(label, value, unit, sub) {
  return h("div", { class: "tile" }, h("div", { class: "label", text: label }), h("div", { class: "value" }, value, unit ? h("small", { text: unit }) : null), sub ? h("div", { class: "delta", text: sub }) : null);
}
function renderKPIs() {
  const box = clear($("kpis"));
  const r = state.result;
  if (!r) return box.append(h("p", { class: "empty", text: "지도를 불러오면 후보 단지를 계산합니다." }));
  const s = r.summary;
  box.append(tile("후보 단지", nf0.format(s.n_complexes), "곳", `계산 ${nf2.format(s.seconds)}초`));
  box.append(tile("단지 면적", nf1.format(s.complex_area_ha), "ha", `적격 농지 ${nf1.format(s.eligible_area_ha)}ha의 ${nf0.format(s.coverage * 100)}%`));
  box.append(tile("포함 농가", nf0.format(s.owners_included), "명", state.region.meta.has_owner ? "소유자 속성 기준" : "필지 수로 추정"));
  box.append(tile("연 소득 증분(기준)", nf1.format(s.income_musd[1]), "백만원", `보수 ${nf1.format(s.income_musd[0])} ~ 낙관 ${nf1.format(s.income_musd[2])}`));
}
function renderRank() {
  const box = clear($("rank"));
  const r = state.result;
  if (!r) return;
  if (!r.complexes.length) {
    box.append(h("p", { class: "empty", text: "조건을 모두 만족하는 단지가 없습니다. 최소 면적이나 참여 농가 수를 낮춰 보세요." }));
    return;
  }
  const head = ["순위", "면적", "필지", "농가", "경사", "도로", "배수", "소득", "점수"];
  const rows = r.complexes.map((c) =>
    h(
      "tr",
      { class: c.id === state.selected ? "sel" : "", tabindex: 0, onclick: () => selectComplex(c.id, true), onkeydown: (e) => e.key === "Enter" && selectComplex(c.id, true) },
      h("td", { text: String(c.id) }),
      h("td", { text: nf1.format(c.area_ha) }),
      h("td", { text: nf0.format(c.n_parcels) }),
      h("td", { text: `${nf0.format(c.n_owners)}${c.owner_mode === "proxy" ? "*" : ""}` }),
      h("td", { text: `${nf1.format(c.mean_slope)}%` }),
      h("td", { text: nf0.format(c.road_parcels) }),
      h("td", { text: `${c.drain_class} ${nf0.format(c.homog * 100)}%` }),
      h("td", { text: nf1.format(c.income_musd[1]) }),
      h("td", { text: nf1.format(c.score) })
    )
  );
  box.append(h("div", { class: "tablewrap" }, h("table", { class: "data rank" }, h("thead", {}, h("tr", {}, head.map((t) => h("th", { text: t })))), h("tbody", {}, rows))));
  box.append(h("p", { class: "hint", text: "면적 ha, 소득은 기준 시나리오 백만원/년. 농가 수의 *는 필지 수로 추정한 값입니다." }));
}
function selectComplex(k, zoom) {
  state.selected = k;
  restyle();
  renderRank();
  renderDetail();
  if (zoom && state.result) {
    const c = state.result.complexes.find((x) => x.id === k);
    const b = L.latLngBounds([]);
    for (const p of c.parcels) {
      const l = layerByPid.get(p);
      if (l) b.extend(l.getBounds());
    }
    if (b.isValid()) map.fitBounds(b, { padding: [40, 40], maxZoom: 17 });
  }
}
function renderDetail() {
  const box = clear($("detail"));
  const r = state.result;
  const c = r && r.complexes.find((x) => x.id === state.selected);
  $("detailTitle").textContent = c ? `단지 ${c.id} 상세` : "단지 상세";
  if (!c) return box.append(h("p", { class: "empty", text: "순위표나 지도에서 단지를 고르세요." }));
  const P = pal();
  // 제약 점검
  const checks = Object.entries(c.checks).map(([name, [val, rule]]) => h("tr", {}, h("td", { text: name }), h("td", { text: String(val) }), h("td", { text: rule }), h("td", { class: "ok", text: "✓ 충족" })));
  box.append(h("div", { class: "tablewrap" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["제약", "값", "기준", "판정"].map((t) => h("th", { text: t })))), h("tbody", {}, checks))));
  // 지표
  box.append(
    h(
      "dl",
      { class: "kv" },
      h("dt", { text: "기계화 적합도" }), h("dd", { text: nf2.format(c.mech_index) }),
      h("dt", { text: "집단화 개선도(1−K)" }), h("dd", { text: `${nf2.format(c.consolidation)} (묶기 전 K ${nf2.format(c.januszewski_before)} → 1)` }),
      h("dt", { text: "최대 경사" }), h("dd", { text: `${nf1.format(c.max_slope)}%` }),
      h("dt", { text: "참여 농가" }), h("dd", { text: `${nf0.format(c.n_owners)}명${c.owner_mode === "proxy" ? "(추정)" : ""}` })
    )
  );
  // 소득 3구간 막대
  const W = Math.max(280, Math.round(box.clientWidth || 360));
  const max = Math.max(...r.complexes.map((x) => x.income_musd[2]), 1);
  const x0 = 8;
  const sc = (v) => x0 + (v / max) * (W - 2 * x0 - 30);
  const [lo, mid, hi] = c.income_musd;
  const svg = sv("svg", { viewBox: `0 0 ${W} 54`, role: "img", "aria-label": `연 소득 증분 보수 ${lo} 기준 ${mid} 낙관 ${hi} 백만원` });
  svg.append(sv("line", { x1: x0, x2: W - x0, y1: 26, y2: 26, stroke: P.axis, "stroke-width": 1 }));
  svg.append(sv("line", { x1: sc(lo), x2: sc(hi), y1: 26, y2: 26, stroke: P.s1, "stroke-width": 6, "stroke-linecap": "round" }));
  svg.append(sv("circle", { cx: sc(mid), cy: 26, r: 6, fill: P.s1, stroke: P.surface, "stroke-width": 2 }));
  svg.append(sv("text", { x: sc(lo), y: 48, "font-size": 11, fill: P.text2, "text-anchor": "middle", text: `보수 ${nf1.format(lo)}` }));
  svg.append(sv("text", { x: sc(mid), y: 14, "font-size": 11, fill: P.text, "font-weight": 600, "text-anchor": "middle", text: `기준 ${nf1.format(mid)}` }));
  svg.append(sv("text", { x: sc(hi), y: 48, "font-size": 11, fill: P.text2, "text-anchor": "middle", text: `낙관 ${nf1.format(hi)}` }));
  box.append(h("h3", { text: "연 소득 증분(백만원) — 점 추정이 아닌 3구간" }), svg);
}

// ------------------------------------------------------------------ 검증
async function runValidate() {
  if (!state.region) return;
  const box = clear($("validation"));
  box.append(h("p", { class: "empty", text: "계산 중… 민감도 분석을 위해 엔진을 여러 번 돌립니다." }));
  try {
    const v = await api("/api/sites/validate", { region_id: state.region.region_id, settings: readSettings() });
    clear(box);
    if (v.truth_synthetic) box.append(h("p", { class: "hint", text: "정답 단지가 생성기가 심어 둔 가상 경계입니다. 절차 시연용 수치이며 성능 주장이 아닙니다." }));
    const head = ["방법", "필지 F1", "정밀도", "재현율", "평균 IoU", "제약 충족 단지"];
    box.append(
      h("div", { class: "tablewrap" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, head.map((t) => h("th", { text: t })))),
        h("tbody", {}, v.table.map((row) => h("tr", {}, h("td", { text: row.method }), h("td", { text: nf2.format(row.f1) }), h("td", { text: nf2.format(row.precision) }), h("td", { text: nf2.format(row.recall) }), h("td", { text: nf2.format(row.iou_mean) }), h("td", { text: `${row.feasible}/${row.complexes}` }))))))
    );
    const sens = v.sensitivity;
    box.append(h("h3", { text: `민감도: 선정 필지 Jaccard 평균 ${nf2.format(sens.mean)}, 최소 ${nf2.format(sens.min)}` }));
    box.append(h("div", { class: "tablewrap" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["조건 변화", "Jaccard", "단지 수", "면적(ha)"].map((t) => h("th", { text: t })))),
      h("tbody", {}, sens.rows.map((x) => h("tr", {}, h("td", { text: x.case }), h("td", { text: nf2.format(x.jaccard) }), h("td", { text: String(x.n_complexes) }), h("td", { text: nf1.format(x.area_ha) })))))));
  } catch (err) {
    clear(box).append(h("p", { class: "empty", text: `검증하지 못했습니다: ${err.message}` }));
  }
}

// ------------------------------------------------------------------ 동작
async function runSolve() {
  if (!state.region) return;
  const seq = ++state.reqSeq;
  setBusy(true, "후보 단지를 계산하는 중…");
  try {
    const res = await api("/api/sites/solve", { region_id: state.region.region_id, settings: readSettings() });
    if (seq !== state.reqSeq) return;
    state.result = res;
    if (state.selected && !res.complexes.some((c) => c.id === state.selected)) state.selected = null;
    $("solveInfo").textContent = `단지 ${res.summary.n_complexes}곳 · ${nf2.format(res.summary.seconds)}초`;
    renderAllResults();
    showAlert(regionWarnings());
  } catch (err) {
    if (seq === state.reqSeq) showAlert(`계산하지 못했습니다: ${err.message}`);
  } finally {
    if (seq === state.reqSeq) setBusy(false);
  }
}
function renderAllResults() {
  restyle();
  renderKPIs();
  renderRank();
  renderDetail();
}
function setRegion(reg) {
  state.region = reg;
  state.result = null;
  state.selected = null;
  renderParcels();
  renderAllResults();
  clear($("validation")).append(h("p", { class: "empty", text: reg.meta.truth_complexes ? "정답 단지 재현(F1·IoU), 베이스라인 비교, 민감도를 계산합니다." : "정답 단지 속성이 없어 재현 검증은 할 수 없습니다." }));
  $("btnValidate").disabled = !reg.meta.truth_complexes;
  const m = reg.meta;
  setStatus(`${m.name} · 필지 ${nf0.format(m.n_parcels)}개 · ${nf1.format(m.total_area_ha)}ha`);
  const used = (m.sources || []).filter((s) => s.status && s.status.used).map((s) => s.name);
  $("mapNote").textContent = m.synthetic ? "가상 데이터 · 실제 지형·소유·단지와 무관" : used.length ? `실측 자료: ${used.join(" · ")}` : "불러온 지도";
  showAlert(regionWarnings());
  if (!m.has_owner) document.querySelector('input[name="ownerMode"][value="proxy"]').checked = true;
  renderSources(m);
}
function regionWarnings() {
  const w = (state.region && state.region.meta.warnings) || [];
  return w.length ? `확인 필요: ${w.join(" ")}` : "";
}
function renderSources(m) {
  const box = clear($("sources"));
  if (m.synthetic) return box.append(h("p", { class: "empty", text: "가상 읍면 생성기가 만든 합성 데이터입니다. 실측 지도를 고르면 출처를 보여 줍니다." }));
  const list = m.sources || [];
  if (!list.length) return box.append(h("p", { class: "empty", text: "불러온 파일에 출처 정보가 없습니다." }));
  box.append(
    h("ul", { class: "srclist" }, list.map((s) => {
      const st = s.status || {};
      const label = st.used ? "사용" : `미사용${st.reason ? ` · ${st.reason}` : ""}`;
      return h(
        "li", {},
        h("span", { class: `st ${st.used ? "on" : "off"}`, text: label }),
        s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener", text: s.name }) : s.name,
        h("div", { class: "what", text: `${(s.fields || []).join(", ")} — ${s.provider}${s.key_required && !st.used ? ` (${s.key_required} 필요)` : ""}` })
      );
    }))
  );
  if (m.built) box.append(h("p", { class: "hint", text: `지도 생성일 ${m.built}. 출처별 이용 조건은 docs/real-data.md에 정리했습니다.` }));
}
async function loadReal() {
  const name = $("realSelect").value;
  if (!name) return;
  setBusy(true, "실측 지도를 불러오는 중…");
  try {
    setRegion(await api("/api/sites/region", { name }));
    await runSolve();
  } catch (err) {
    showAlert(`실측 지도를 불러오지 못했습니다: ${err.message}`);
  } finally {
    setBusy(false);
  }
}
async function listReal() {
  const sel = clear($("realSelect"));
  try {
    const regions = await apiGet("/api/sites/regions");
    for (const r of regions) sel.append(h("option", { value: r.name, text: `${r.title} (필지 ${nf0.format(r.n_parcels)}개)` }));
    if (!regions.length) sel.append(h("option", { value: "", text: "실측 지도 없음" }));
    $("btnReal").disabled = !regions.length;
    return regions;
  } catch (_) {
    sel.append(h("option", { value: "", text: "목록을 읽지 못했습니다" }));
    $("btnReal").disabled = true;
    return [];
  }
}
async function loadDemo() {
  setBusy(true, "가상 읍면을 만드는 중…");
  try {
    setRegion(await api("/api/sites/demo", { seed: Math.round(Number($("seed").value) || 11), n_farmers: Math.round(Number($("farmers").value) || 150) }));
    await runSolve();
  } catch (err) {
    showAlert(`가상 읍면을 만들지 못했습니다: ${err.message}`);
  } finally {
    setBusy(false);
  }
}
async function importFile(file) {
  setBusy(true, "지도를 읽고 인접 그래프를 만드는 중…");
  try {
    setRegion(await api("/api/sites/import", JSON.parse(await file.text())));
    await runSolve();
  } catch (err) {
    showAlert(`파일을 불러오지 못했습니다: ${err.message}`);
  } finally {
    setBusy(false);
    $("fileInput").value = "";
  }
}
function downloadCsv() {
  const r = state.result;
  if (!r) return;
  const feats = state.region.parcels.features;
  const lines = ["complex,parcel_id,pnu,owner,area_ha,slope,drainage,road_access"];
  for (const c of r.complexes) {
    for (const p of c.parcels) {
      const f = feats[idx.get(p)];
      const q = f.properties;
      lines.push([c.id, p, q.pnu || "", q.owner || "", areaHa(f).toFixed(3), q.slope ?? "", q.drainage ?? "", q.road_access ? 1 : 0].join(","));
    }
  }
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv" });
  const a = h("a", { href: URL.createObjectURL(blob), download: "candidate-complex-parcels.csv" });
  document.body.append(a);
  a.click();
  a.remove();
}

async function boot() {
  const q = new URLSearchParams(location.search);
  if (q.get("theme") === "dark" || q.get("theme") === "light") document.documentElement.dataset.theme = q.get("theme");
  for (const d of CONSTRAINTS) if (q.has(d.key)) d.value = Number(q.get(d.key));
  if (q.has("seed")) $("seed").value = q.get("seed");
  buildSliders("constraintSliders", CONSTRAINTS);
  buildSliders("weightSliders", WEIGHTS);
  initMap();
  for (const id of ["requireRoad", "includeUpland"]) $(id).addEventListener("change", scheduleSolve);
  for (const el of document.querySelectorAll('input[name="ownerMode"]')) el.addEventListener("change", scheduleSolve);
  $("showTruth").addEventListener("change", restyle);
  $("showRoads").addEventListener("change", renderRoads);
  $("chkBasemap").addEventListener("change", (e) => toggleBasemap(e.target.checked));
  $("btnDemo").addEventListener("click", loadDemo);
  $("btnReal").addEventListener("click", loadReal);
  $("fileInput").addEventListener("change", (e) => e.target.files[0] && importFile(e.target.files[0]));
  $("btnValidate").addEventListener("click", runValidate);
  $("btnCsv").addEventListener("click", downloadCsv);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { renderRoads(); renderAllResults(); });
  if (q.has("truth")) $("showTruth").checked = true;
  // 기본은 첫 실측 지도. ?map=demo 면 가상 읍면, ?map=<이름> 이면 그 실측 지도
  const regions = await listReal();
  const want = q.get("map");
  if (want && want !== "demo" && regions.some((r) => r.name === want)) $("realSelect").value = want;
  if (want === "demo" || !regions.length) await loadDemo();
  else await loadReal();
  if (q.has("select") && state.result && state.result.complexes.length) selectComplex(Number(q.get("select")) || 1, true);
  if (q.has("validate")) await runValidate();
}
boot();
