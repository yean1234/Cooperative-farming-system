/* 빈 필드 시뮬레이터
 * 서버 없이 브라우저에서 계산한다. 가상 들녘(필지 0.3ha 격자) 위에서
 * 거절 농가를 해제하면 지원 기준 판정, 대체 필지 후보, 기계비, 배당금을 다시 계산한다.
 *
 * 경제 근거 (국가데이터처 2025년산 논벼 생산비조사, 10a당 → ha당 만원):
 *   총수입 2020~2025년산 최저 1,152.9 · 평균 1,236.1 · 최고 1,348.7
 *   법인 규모(5ha 이상) 직접생산비 508 + 법인 운영비 30(가정) = 538
 *   토지용역비 280,283원/10a → 평당 927원 (고정 임차료 기본값)
 */
"use strict";

const P = {
  COLS: 12, ROWS: 16,
  PW: 100, PH: 30,            // 필지 폭·높이(m)
  HA: 0.3,                    // 필지 1개 면적(ha)
  PY_PER_HA: 3025,
  REV: { lo: 1152.9, mid: 1236.1, hi: 1348.7 },   // 만원/ha
  COST: 538,                  // 만원/ha
  OPS: 8, SETUP_H: 0.25, TURN_H: 2 / 60, MACH_WON_H: 60000, SPEED_KMH: 12, DETOUR: 1.3,
  RENT_PY: 927, BONUS: 0.5,
  SEED: 7,
};

// ---------------------------------------------------------------- 들녘 만들기
function rng(seed) {
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
function shuffle(arr, r) { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(r() * (i + 1)); [arr[i], arr[j]] = [arr[j], arr[i]]; } return arr; }

const MEMBER_NAMES = "가나다라마바사아자차카타파".split("").map((c) => `농가 ${c}`);

function buildField(seed) {
  const r = rng(seed);
  const { COLS, ROWS } = P;
  const inRegion = (y, x) => y >= 3 && y <= 12 && x >= 2 && x <= 9 && !((y === 3 || y === 12) && (x === 2 || x === 9));
  const parcels = [];
  for (let y = 0; y < ROWS; y++) for (let x = 0; x < COLS; x++) parcels.push({ id: y * COLS + x, x, y, owner: null, member: inRegion(y, x) });
  const at = (y, x) => parcels[y * COLS + x];

  // 참여 농가: 단지 안 세로 2필지 묶음을 섞어 13농가에 돌아가며 나눈다 (소유지가 흩어지도록)
  const chunks = [];
  for (let y = 3; y <= 11; y += 2) for (let x = 2; x <= 9; x++) {
    const c = [at(y, x), at(y + 1, x)].filter((p) => p.member);
    if (c.length) chunks.push(c);
  }
  shuffle(chunks, r).forEach((c, i) => c.forEach((p) => { p.owner = MEMBER_NAMES[i % MEMBER_NAMES.length]; }));

  // 비참여 농가: 단지 밖 필지를 구역별로 1~2농가에 묶는다 (가까운 땅끼리)
  const buckets = new Map();
  parcels.filter((p) => !p.member).forEach((p) => {
    const k = Math.floor(p.y / 4) * 4 + Math.floor(p.x / 3);
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k).push(p);
  });
  let n = 0;
  [...buckets.values()].forEach((list) => {
    const split = list.length > 5 && r() < 0.6;
    const a = `비참여 ${++n}`, b = split ? `비참여 ${++n}` : a;
    list.sort((p, q) => p.x - q.x || p.y - q.y).forEach((p, i) => { p.owner = i < list.length / 2 ? a : b; });
  });

  const farms = new Map();
  parcels.forEach((p) => { if (!farms.has(p.owner)) farms.set(p.owner, { name: p.owner, member: p.member, parcels: [] }); farms.get(p.owner).parcels.push(p.id); });
  return { parcels, farms, depot: [-60, ROWS * P.PH + 60] };
}

// ---------------------------------------------------------------- 기하
const px = (p) => p.x * P.PW + Math.floor(p.x / 2) * 8;          // 2열마다 농로 8m
const py = (p) => p.y * P.PH + Math.floor(p.y / 4) * 6;          // 4행마다 농로 6m
const cx = (p) => px(p) + P.PW / 2, cy = (p) => py(p) + P.PH / 2;
function neighbors(p) {
  const out = [];
  if (p.x > 0) out.push(p.id - 1); if (p.x < P.COLS - 1) out.push(p.id + 1);
  if (p.y > 0) out.push(p.id - P.COLS); if (p.y < P.ROWS - 1) out.push(p.id + P.COLS);
  return out;
}
function components(set) {
  const seen = new Set(), comps = [];
  for (const id of set) {
    if (seen.has(id)) continue;
    const stack = [id], comp = []; seen.add(id);
    while (stack.length) { const c = stack.pop(); comp.push(c); for (const nb of neighbors(F.parcels[c])) if (set.has(nb) && !seen.has(nb)) { seen.add(nb); stack.push(nb); } }
    comps.push(comp);
  }
  return comps;
}
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
function tour(depot, pts) {
  // 최근접 이웃 + 2-opt, 보관소에서 출발해 돌아온다
  const rest = pts.slice(), path = [depot];
  let cur = depot;
  while (rest.length) { let bi = 0; rest.forEach((q, i) => { if (dist(cur, q) < dist(cur, rest[bi])) bi = i; }); cur = rest.splice(bi, 1)[0]; path.push(cur); }
  path.push(depot);
  let improved = true;
  while (improved) {
    improved = false;
    for (let i = 1; i < path.length - 2; i++) for (let k = i + 1; k < path.length - 1; k++) {
      const d0 = dist(path[i - 1], path[i]) + dist(path[k], path[k + 1]);
      const d1 = dist(path[i - 1], path[k]) + dist(path[i], path[k + 1]);
      if (d1 + 1e-6 < d0) { path.splice(i, k - i + 1, ...path.slice(i, k + 1).reverse()); improved = true; }
    }
  }
  let len = 0; for (let i = 1; i < path.length; i++) len += dist(path[i - 1], path[i]);
  return { path, km: len / 1000 };
}

// ---------------------------------------------------------------- 평가
function evaluate(set, extra = {}) {
  const area = set.size * P.HA;
  const comps = components(set);
  const cents = comps.map((c) => { let sx = 0, sy = 0, a = 0; c.forEach((id) => { const p = F.parcels[id]; sx += cx(p); sy += cy(p); a++; }); return [sx / a, sy / a]; });
  const t = tour(F.depot, cents);
  let edges = 0;
  for (const id of set) for (const nb of neighbors(F.parcels[id])) if (!set.has(nb)) edges++;
  const setupH = comps.length * P.OPS * P.SETUP_H;
  const travelH = t.km * P.DETOUR * P.OPS / P.SPEED_KMH;
  const turnH = edges * P.OPS * P.TURN_H;
  const fieldH = area * P.OPS * 0.4;
  const machineWon = (setupH + travelH + turnH) * P.MACH_WON_H;
  const pyeong = area * P.PY_PER_HA;
  const per = {};
  for (const k of ["lo", "mid", "hi"]) per[k] = pyeong ? ((area * (P.REV[k] - P.COST) * 1e4 - machineWon) / pyeong) : 0;
  const farmSet = new Set([...set].map((id) => F.parcels[id].owner));
  const memberFarms = [...farmSet].filter((f) => F.farms.get(f).member || (extra.added || []).includes(f));
  return {
    set, area, blocks: comps.length, routeKm: t.km * P.DETOUR, path: t.path, edges,
    hours: setupH + travelH + turnH + fieldH, machineWon, per, pyeong,
    farms: memberFarms.length, net: { mid: area * (P.REV.mid - P.COST) * 1e4 - machineWon },
  };
}
function verdict(per, target) { return per.lo >= target ? "safe" : per.mid >= target ? "ok" : "bad"; }
const VERD = { safe: ["안전", "safe"], ok: ["가능", "ok"], bad: ["미달", "bad"] };
const RANK = { safe: 0, ok: 1, bad: 2 };

// ---------------------------------------------------------------- 상태
const S = { refused: new Set(), areaMin: 20, farmMin: 5, target: 2000, pay: "div", shock: 20, sel: null, tab: 1, hoverFarm: null };
let F = buildField(P.SEED);
let VIEW = null; // 마지막 계산 결과

function memberParcels() { return F.parcels.filter((p) => p.member).map((p) => p.id); }

function buildOptions() {
  const base = new Set(memberParcels().filter((id) => !S.refused.has(F.parcels[id].owner)));
  const holes = memberParcels().filter((id) => S.refused.has(F.parcels[id].owner));
  const opts = [];
  const mk = (key, name, sub, set, persuade, meta = {}) => {
    const ev = evaluate(set, meta);
    const meets = ev.area + 1e-9 >= S.areaMin && ev.farms >= S.farmMin;
    const why = [];
    if (ev.area + 1e-9 < S.areaMin) why.push(`면적 ${(S.areaMin - ev.area).toFixed(1)}ha 부족`);
    if (ev.farms < S.farmMin) why.push(`농가 ${S.farmMin - ev.farms}명 부족`);
    opts.push({ key, name, sub, persuade, meets, why: why.join(" · "), v: verdict(ev.per, S.target), ...meta, ev });
  };

  if (!S.refused.size) {
    mk("base", "기본 필지화", "전원 참여 · 구멍 없음", base, 0);
    return { opts, base, holes };
  }
  mk("keep", "그대로", "땅 추가 없이 경로만 다시 짬", base, 0);

  // 경작권 교환: 구멍 필지를 법인이 짓고, 같은 수의 가장자리 필지를 거절 농가에 맡긴다
  const holeSet = new Set(holes);
  const nearHole = (id) => neighbors(F.parcels[id]).some((nb) => holeSet.has(nb));
  const coopNb = (id) => neighbors(F.parcels[id]).filter((nb) => base.has(nb)).length;
  // 가장자리 = 단지 안 이웃이 적은 필지. 구멍 바로 옆 필지는 넘기지 않는다.
  const edgeCands = [...base].filter((id) => !nearHole(id))
    .sort((a, b) => coopNb(a) - coopNb(b) || distFromCenter(b) - distFromCenter(a));
  let exSet = null, exGive = [];
  if (edgeCands.length >= holes.length) {
    exGive = edgeCands.slice(0, holes.length);
    exSet = new Set([...base].filter((id) => !exGive.includes(id)).concat(holes));
    mk("ex", "경작권 교환", `구멍 ${holes.length}필지 ↔ 가장자리 ${holes.length}필지`, exSet, S.refused.size, { exIn: holes, exOut: exGive });
  }

  // 땅 추가: 단지에 닿아 있는 비참여 농가를 농가 단위로 편입
  const touching = new Set();
  for (const id of base) for (const nb of neighbors(F.parcels[id])) { const p = F.parcels[nb]; if (!p.member) touching.add(p.owner); }
  const cand = [...touching].map((f) => {
    const ids = F.farms.get(f).parcels;
    const set = new Set([...base, ...ids]);
    return { f, ids, ev: evaluate(set, { added: [f] }) };
  }).sort((a, b) => a.ev.blocks - b.ev.blocks || b.ev.area - a.ev.area || b.ev.per.mid - a.ev.per.mid);
  const top = cand.slice(0, 4);
  top.forEach((c) => mk(`add:${c.f}`, `${c.f} 편입`, `+${(c.ids.length * P.HA).toFixed(1)}ha · 경로 다시 짬`, new Set([...base, ...c.ids]), 1, { added: [c.f], addIds: c.ids }));
  for (let i = 0; i < Math.min(3, top.length); i++) for (let j = i + 1; j < Math.min(3, top.length); j++) {
    const a = top[i], b = top[j], ids = [...a.ids, ...b.ids];
    mk(`add2:${a.f}+${b.f}`, `${a.f} + ${b.f} 편입`, `+${(ids.length * P.HA).toFixed(1)}ha`, new Set([...base, ...ids]), 2, { added: [a.f, b.f], addIds: ids });
  }
  if (exSet && top.length) {
    const c = top[0];
    mk(`exadd:${c.f}`, `교환 + ${c.f} 편입`, `구멍 메움 + ${(c.ids.length * P.HA).toFixed(1)}ha`, new Set([...exSet, ...c.ids]), S.refused.size + 1, { exIn: holes, exOut: exGive, added: [c.f], addIds: c.ids });
  }
  return { opts, base, holes, candFarms: top.map((c) => c.f) };
}
let CENTER = null;
function distFromCenter(id) {
  if (!CENTER) { const ids = memberParcels(); CENTER = [ids.reduce((s, i) => s + cx(F.parcels[i]), 0) / ids.length, ids.reduce((s, i) => s + cy(F.parcels[i]), 0) / ids.length]; }
  const p = F.parcels[id]; return dist([cx(p), cy(p)], CENTER);
}

function rankOptions(opts) {
  const ok = opts.filter((o) => o.meets).sort((a, b) => RANK[a.v] - RANK[b.v] || a.persuade - b.persuade || b.ev.per.mid - a.ev.per.mid);
  const no = opts.filter((o) => !o.meets).sort((a, b) => b.ev.area - a.ev.area);
  ok.forEach((o, i) => { o.rank = i + 1; });
  return ok.concat(no);
}

// ---------------------------------------------------------------- 렌더링 도우미
const $ = (id) => document.getElementById(id);
const won = (v) => `${Math.round(v).toLocaleString("ko-KR")}원`;
const man = (v) => `${Math.round(v / 1e4).toLocaleString("ko-KR")}만 원`;
const ha = (v) => `${v.toFixed(1)}ha`;
const chip = (v) => `<span class="chip ${VERD[v][1]}">${VERD[v][0]}</span>`;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SVGNS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, parent) { const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }

// ---------------------------------------------------------------- 메인 계산·그리기
function recompute() {
  CENTER = null;
  const { opts, holes, candFarms } = buildOptions();
  const ranked = rankOptions(opts);
  const keep = opts.find((o) => o.key === "keep" || o.key === "base");
  const stage = !S.refused.size ? 0 : keep.meets ? 1 : 2;
  if (!ranked.some((o) => o.key === S.sel)) S.sel = (ranked.find((o) => o.rank === 1) || ranked[0]).key;
  const sel = ranked.find((o) => o.key === S.sel);
  const full = evaluate(new Set(memberParcels()));
  VIEW = { ranked, sel, stage, holes, candFarms: candFarms || [], keep, full };
  renderStepper(); renderBanner(); renderJudge(); renderMap(); renderOptions(); renderMachine(); renderPayout();
}

function renderStepper() {
  document.querySelectorAll("#stepper li").forEach((li) => {
    const on = Number(li.dataset.stage) === VIEW.stage;
    li.classList.toggle("on", on);
    if (on) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  });
}

function renderBanner() {
  const b = $("banner"), { stage, keep, holes } = VIEW;
  const nRef = S.refused.size, holeHa = holes.length * P.HA;
  b.className = `banner s${stage}`;
  if (stage === 0) b.innerHTML = `<b>① 기본 필지화 · 전원 참여</b>참여 농가 ${keep.ev.farms}곳, ${ha(keep.ev.area)}. 이 결과가 모든 비교의 기준선입니다. 지도에서 참여 농가를 눌러 거절을 시험해 보세요.`;
  else if (stage === 1) b.innerHTML = `<b>② 구멍이 났지만 지원 기준 충족</b>거절 ${nRef}농가 · ${ha(holeHa)}가 빠져도 ${ha(keep.ev.area)}로 기준(${S.areaMin}ha)을 넘습니다. 그대로 가도 되며, 아래에서 더 나은 대안과 비교할 수 있습니다.`;
  else b.innerHTML = `<b>③ 구멍 때문에 지원 기준 미충족</b>거절 ${nRef}농가 · ${ha(holeHa)}가 빠져 ${keep.why}. 땅 추가가 들어간 대안만 기준을 넘깁니다.`;
}

function renderJudge() {
  const o = VIEW.sel, e = o.ev;
  $("judgeOf").textContent = `· ${o.name}`;
  const areaPct = Math.min(100, e.area / Math.max(S.areaMin * 1.25, e.area) * 100);
  const minPct = S.areaMin / Math.max(S.areaMin * 1.25, e.area) * 100;
  const areaOk = e.area + 1e-9 >= S.areaMin, farmOk = e.farms >= S.farmMin, conn = e.blocks === 1;
  const lo = 1600, hi = 2800, pos = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
  $("judge").innerHTML = `
    <div class="crit">
      <span>면적</span><span class="v ${areaOk ? "pass" : "fail"}">${ha(e.area)} ${areaOk ? "✓" : "✗"}</span>
    </div>
    <div class="gauge" aria-hidden="true"><i style="width:${areaPct}%;background:${areaOk ? "var(--coop)" : "var(--hole)"}"></i><em style="left:${minPct}%"></em></div>
    <div class="scale"><span>0</span><span>기준 ${S.areaMin}ha</span></div>
    <div class="crit" style="margin-top:8px">
      <span>참여 농가</span><span class="v ${farmOk ? "pass" : "fail"}">${e.farms}명 ${farmOk ? "✓" : "✗"}</span>
      <span>연결성 (권장)</span><span class="v ${conn ? "pass" : "soft"}">${conn ? "한 덩어리 ✓" : `블록 ${e.blocks}개 !`}</span>
    </div>
    <h3 style="margin-top:12px">평당 배당 범위 ${chip(o.v)}</h3>
    <div class="range" role="img" aria-label="평당 배당 ${won(e.per.lo)}에서 ${won(e.per.hi)}, 기준 ${won(e.per.mid)}, 목표 ${won(S.target)}">
      <div class="track"></div>
      <div class="band" style="left:${pos(e.per.lo)}%;width:${pos(e.per.hi) - pos(e.per.lo)}%"></div>
      <div class="dot" style="left:${pos(e.per.mid)}%"></div>
      <div class="tgt" style="left:${pos(S.target)}%" title="목표"></div>
    </div>
    <div class="scale"><span>${won(e.per.lo)}</span><span><b>기준 ${won(e.per.mid)}</b></span><span>${won(e.per.hi)}</span></div>
    <p class="hint">검은 선 = 목표 ${won(S.target)} · 전원 참여 기준선 ${won(VIEW.full.per.mid)}</p>`;
}

// 지도는 가로를 줄여 그린다(필지가 실제로는 100m × 30m라 너무 납작해 보이므로). 거리 계산은 미터 그대로 쓴다.
const SX = 0.56;
function renderMap() {
  const svg = $("map"), { sel, holes, candFarms } = VIEW;
  const last = F.parcels[F.parcels.length - 1];
  const W = (px(last) + P.PW) * SX + 20, H = py(last) + P.PH + 110;
  svg.setAttribute("viewBox", `-70 -20 ${W + 70} ${H}`);
  svg.innerHTML = "";
  const exIn = new Set(sel.exIn || []), exOut = new Set(sel.exOut || []), add = new Set(sel.addIds || []);
  const candSet = new Set();
  candFarms.forEach((f) => F.farms.get(f).parcels.forEach((id) => candSet.add(id)));
  const holeSet = new Set(holes);
  const g = el("g", {}, svg);
  F.parcels.forEach((p) => {
    let fill = "var(--other)", stroke = "#cfccc2", dash = "", cls = "parcel", label = "";
    if (p.member && !holeSet.has(p.id)) { fill = "var(--coop-soft)"; stroke = "var(--coop)"; cls += " member"; }
    if (holeSet.has(p.id)) { fill = "var(--hole-soft)"; stroke = "var(--hole)"; cls += " member"; label = "거절"; }
    if (exIn.has(p.id)) { fill = "var(--ex-soft)"; stroke = "var(--ex)"; label = "교환"; }
    if (exOut.has(p.id)) { fill = "#fff"; stroke = "var(--ex)"; dash = "5 3"; label = "→거절농가"; }
    if (!p.member && candSet.has(p.id) && !add.has(p.id)) { fill = "#fff"; stroke = "var(--cand)"; dash = "5 3"; }
    if (add.has(p.id)) { fill = "var(--cand-soft)"; stroke = "var(--cand)"; label = "편입"; }
    if (S.hoverFarm && p.owner === S.hoverFarm) cls += " hl";
    const r = el("rect", { x: px(p) * SX, y: py(p), width: P.PW * SX - 2, height: P.PH - 2, rx: 2, fill, stroke, class: cls, "data-id": p.id }, g);
    if (dash) r.setAttribute("stroke-dasharray", dash);
    if (p.member) { r.setAttribute("tabindex", "0"); r.setAttribute("role", "button"); r.setAttribute("aria-label", `${p.owner} 필지, ${S.refused.has(p.owner) ? "거절됨. 눌러서 복구" : "참여 중. 눌러서 거절"}`); }
    const tt = el("title", {}, r); tt.textContent = `${p.owner}${p.member ? (S.refused.has(p.owner) ? " · 거절" : " · 참여") : " · 비참여"}`;
    if (label) { const t = el("text", { x: (px(p) + P.PW / 2) * SX - 1, y: py(p) + P.PH / 2 + 5, "text-anchor": "middle", "font-size": 13, "font-weight": 700, fill: stroke, "pointer-events": "none" }, g); t.textContent = label; }
  });
  // 경로
  const pts = sel.ev.path.map((q) => `${q[0] * SX},${q[1]}`).join(" ");
  el("polyline", { points: pts, fill: "none", stroke: "var(--ink)", "stroke-width": 2.5, "stroke-dasharray": "9 6", "stroke-linejoin": "round", "pointer-events": "none" }, svg);
  const [dx, dy] = F.depot;
  el("rect", { x: dx * SX - 12, y: dy - 12, width: 24, height: 24, fill: "var(--ink)" }, svg);
  const t = el("text", { x: dx * SX + 18, y: dy + 6, "font-size": 16, fill: "var(--ink)" }, svg); t.textContent = "농기계 보관소";

  const e = sel.ev;
  $("mapNote").innerHTML = `선택한 안: <b>${esc(sel.name)}</b> · ${ha(e.area)} · 블록 ${e.blocks}개 · 트랙터 이동 연 ${(e.routeKm * P.OPS).toFixed(1)}km`;
}

const SHOW = 5;
function renderOptions() {
  const all = VIEW.ranked;
  // 순위권 상위 5개 + 기준 미달 안은 늘 보이고, 나머지 순위권 대안은 접어 둔다
  const visible = all.filter((o, i) => S.showAll || !o.rank || o.rank <= SHOW || o.key === S.sel);
  const hiddenN = all.length - visible.length;
  const rows = visible.map((o) => {
    const e = o.ev, isSel = o.key === S.sel;
    return `<tr class="opt ${isSel ? "sel" : ""} ${o.meets ? "" : "dis"}" data-key="${esc(o.key)}" tabindex="0" aria-selected="${isSel}">
      <td>${o.rank ? `<span class="rank">${o.rank}</span>` : "–"}</td>
      <td>${esc(o.name)}<span class="sub">${esc(o.sub)}</span>${o.meets ? "" : `<span class="why">${esc(o.why)}</span>`}</td>
      <td>${e.area.toFixed(1)}</td>
      <td>${o.persuade}명</td>
      <td><b>${won(e.per.mid)}</b><span class="sub">${Math.round(e.per.lo).toLocaleString("ko-KR")}~${Math.round(e.per.hi).toLocaleString("ko-KR")}</span></td>
      <td>${o.meets ? chip(o.v) : `<span class="chip na">기준 미달</span>`}</td>
    </tr>`;
  }).join("");
  $("optTable").innerHTML = `<table aria-label="대안 비교">
    <thead><tr><th>순위</th><th>대안</th><th>면적(ha)</th><th>설득</th><th>평당 배당</th><th>판정</th></tr></thead>
    <tbody>${rows}</tbody></table>
    ${hiddenN || S.showAll ? `<button type="button" class="btn more" id="btnMore" aria-expanded="${!!S.showAll}">${S.showAll ? "▴ 상위 5개만 보기" : `▾ 나머지 대안 ${hiddenN}개 더 보기`}</button>` : ""}
    <p class="hint">행을 누르면 지도와 왼쪽 판정이 그 안으로 바뀝니다. 평당 배당 아래 숫자는 보수~낙관 범위입니다.</p>`;
}

function bars(items, key, fmt, baseKey) {
  const max = Math.max(...items.map((i) => i[key])) || 1;
  return `<div class="bars">${items.map((i) => `<span>${esc(i.label)}</span><span class="bar ${i.label === baseKey ? "base" : ""}"><i style="width:${i[key] / max * 100}%"></i></span><span class="num">${fmt(i[key])}</span>`).join("")}</div>`;
}
function renderMachine() {
  const { ranked, full, sel } = VIEW;
  // 비교 막대: 전원 참여 기준선, 그대로(있으면), 선택한 안, 순위권 상위 안들
  const pick = [ranked.find((o) => o.key === "keep"), sel, ...ranked.filter((o) => o.meets)].filter(Boolean);
  const uniq = [...new Map(pick.map((o) => [o.key, o])).values()].slice(0, 6);
  const list = [{ label: "전원 참여", ...full }].concat(uniq.map((o) => ({ label: o.name, ...o.ev })));
  const e = sel.ev;
  $("machine").innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="k">작업시간 (연)</div><div class="n">${Math.round(e.hours)}h</div></div>
      <div class="kpi"><div class="k">트랙터 이동 (연)</div><div class="n">${(e.routeKm * P.OPS).toFixed(1)}km</div></div>
      <div class="kpi"><div class="k">부대 기계비 (연)</div><div class="n">${man(e.machineWon)}</div></div>
    </div>
    <h3>작업시간 비교</h3>${bars(list, "hours", (v) => `${Math.round(v)}h`, "전원 참여")}
    <h3>부대 기계비 비교 (세팅 + 이동 + 구멍 주변 회전)</h3>${bars(list, "machineWon", man, "전원 참여")}
    <p class="hint">선택한 안(${esc(sel.name)})의 부대 기계비는 순수익의 ${(e.machineWon / Math.max(1, e.net.mid + e.machineWon) * 100).toFixed(1)}%입니다.</p>`;
}

function payFor(perPy) {
  return {
    div: { member: Math.max(0, perPy), coop: Math.min(0, perPy) },
    rent: { member: P.RENT_PY, coop: perPy - P.RENT_PY },
    mix: { member: P.RENT_PY + P.BONUS * Math.max(0, perPy - P.RENT_PY), coop: perPy - (P.RENT_PY + P.BONUS * Math.max(0, perPy - P.RENT_PY)) },
  };
}
function renderPayout() {
  const e = VIEW.sel.ev, s = S.shock / 100;
  $("shockVal").textContent = s ? `평년 대비 −${S.shock}%` : "평년";
  const perShock = (area) => (area * (P.REV.mid * (1 - s) - P.COST) * 1e4 - e.machineWon) / (area * P.PY_PER_HA);
  const p0 = payFor(e.per.mid), p1 = payFor(perShock(e.area)), pLo = payFor(e.per.lo), pHi = payFor(e.per.hi);
  const names = { div: "배당형", rent: "고정 임차료", mix: "혼합" };
  const row = (k) => `<tr ${k === S.pay ? 'class="sel"' : ""}><td>${names[k]}</td>
      <td><b>${won(p0[k].member)}</b><span class="sub">${Math.round(pLo[k].member) === Math.round(pHi[k].member) ? "고정" : `${Math.round(pLo[k].member).toLocaleString("ko-KR")}~${Math.round(pHi[k].member).toLocaleString("ko-KR")}`}</span></td>
      <td class="${p1[k].member < p0[k].member - 0.5 ? "neg" : ""}"><b>${won(p1[k].member)}</b></td>
      <td class="${p1[k].coop < 0 ? "neg" : "pos"}">${p1[k].coop >= 0 ? "+" : ""}${won(p1[k].coop)}</td></tr>`;
  // 농가별 배당금
  const byFarm = new Map();
  for (const id of e.set) { const f = F.parcels[id].owner; byFarm.set(f, (byFarm.get(f) || 0) + 1); }
  const farmRows = [...byFarm.entries()].sort((a, b) => b[1] - a[1]).map(([f, n]) => {
    const pyeong = n * P.HA * P.PY_PER_HA;
    return `<tr><td>${esc(f)}</td><td>${(n * P.HA).toFixed(1)}</td><td>${man(pyeong * p0[S.pay].member)}</td><td class="${p1[S.pay].member < p0[S.pay].member - 0.5 ? "neg" : ""}">${man(pyeong * p1[S.pay].member)}</td></tr>`;
  }).join("");
  const shockLabel = S.shock ? `흉년 −${S.shock}%` : "평년";
  $("payout").innerHTML = `
    <h3>평당 받는 돈 · ${esc(VIEW.sel.name)}</h3>
    <table aria-label="지급 방식별 평당 금액">
      <thead><tr><th>지급 방식</th><th>평년 (범위)</th><th>${shockLabel}</th><th>법인 유보 (${shockLabel})</th></tr></thead>
      <tbody>${["div", "rent", "mix"].map(row).join("")}</tbody>
    </table>
    <h3 style="margin-top:14px">농가별 배당금 (${names[S.pay]}, 연)</h3>
    <table aria-label="농가별 배당금">
      <thead><tr><th>농가</th><th>면적(ha)</th><th>평년</th><th>${shockLabel}</th></tr></thead>
      <tbody>${farmRows}</tbody>
    </table>
    <p class="hint">흉년은 총수입만 줄이고 생산비는 그대로 둡니다. 배당형은 손실이 나면 0원이고, 그 손실은 법인 유보에 표시됩니다.</p>`;
}

// ---------------------------------------------------------------- 이벤트
function toggleFarm(name) {
  const f = F.farms.get(name);
  if (!f || !f.member) return;
  if (S.refused.has(name)) S.refused.delete(name); else S.refused.add(name);
  S.sel = null;
  recompute();
}
function bindSeg(id, key, conv) {
  $(id).addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    $(id).querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    S[key] = conv(b.dataset.v); recompute();
  });
}
function selectTab(n) {
  S.tab = n;
  [1, 2, 3].forEach((i) => { $(`tab${i}`).setAttribute("aria-selected", String(i === n)); $(`pane${i}`).hidden = i !== n; });
}
function init() {
  $("map").addEventListener("click", (ev) => { const r = ev.target.closest("rect[data-id]"); if (r) toggleFarm(F.parcels[Number(r.dataset.id)].owner); });
  $("map").addEventListener("keydown", (ev) => { if ((ev.key === "Enter" || ev.key === " ") && ev.target.dataset && ev.target.dataset.id) { ev.preventDefault(); toggleFarm(F.parcels[Number(ev.target.dataset.id)].owner); } });
  $("map").addEventListener("mouseover", (ev) => { const r = ev.target.closest("rect[data-id]"); const f = r ? F.parcels[Number(r.dataset.id)].owner : null; if (f !== S.hoverFarm) { S.hoverFarm = f; document.querySelectorAll("#map rect[data-id]").forEach((x) => x.classList.toggle("hl", !!f && F.parcels[Number(x.dataset.id)].owner === f)); } });
  $("map").addEventListener("mouseleave", () => { S.hoverFarm = null; document.querySelectorAll("#map .hl").forEach((x) => x.classList.remove("hl")); });
  $("optTable").addEventListener("click", (ev) => {
    if (ev.target.closest("#btnMore")) { S.showAll = !S.showAll; renderOptions(); $("btnMore")?.focus(); return; }
    const tr = ev.target.closest("tr[data-key]"); if (tr) { S.sel = tr.dataset.key; recompute(); }
  });
  $("optTable").addEventListener("keydown", (ev) => { const tr = ev.target.closest("tr[data-key]"); if (tr && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); S.sel = tr.dataset.key; recompute(); document.querySelector(`tr[data-key="${CSS.escape(S.sel)}"]`)?.focus(); } });
  $("inTarget").addEventListener("input", (ev) => { S.target = Number(ev.target.value) || 0; recompute(); });
  $("inFarmMin").addEventListener("input", (ev) => { S.farmMin = Number(ev.target.value) || 0; recompute(); });
  $("inShock").addEventListener("input", (ev) => { S.shock = Number(ev.target.value); renderPayout(); });
  bindSeg("segArea", "areaMin", Number);
  bindSeg("segPay", "pay", String);
  [1, 2, 3].forEach((i) => $(`tab${i}`).addEventListener("click", () => selectTab(i)));
  $("btnReset").addEventListener("click", () => { S.refused.clear(); S.sel = null; recompute(); });
  recompute();
}
document.addEventListener("DOMContentLoaded", init);
