/* 빈 필드 시뮬레이터
 * 서버 없이 브라우저에서 계산한다. 포항 흥해 들녘 실측 필지(팜맵) 위에서
 * 거절 농가를 해제하면 지원 기준 판정, 대체 필지 후보, 기계비, 배당금을 다시 계산한다.
 * 데이터: data/empty-field-heunghae.js (python -m server.realdata.empty_field 로 생성)
 * 소유자는 추정값이다(농지대장 비공개). 이웃한 필지를 농가당 약 1.8ha로 묶었다.
 *
 * 경제 근거 (국가데이터처 2025년산 논벼 생산비조사, 10a당 → ha당 만원):
 *   총수입 2020~2025년산 최저 1,152.9 · 평균 1,236.1 · 최고 1,348.7
 *   법인 규모(5ha 이상) 직접생산비 508 + 법인 운영비 30(가정) = 538
 *   토지용역비 280,283원/10a → 평당 927원 (고정 임차료 기본값)
 */
"use strict";

const P = {
  PY_PER_HA: 3025,
  REV: { lo: 1152.9, mid: 1236.1, hi: 1348.7 },   // 만원/ha
  COST: 538,                  // 만원/ha
  OPS: 8, SETUP_H: 0.25, TURN_H: 2 / 60, MACH_WON_H: 60000, SPEED_KMH: 12,
  RENT_PY: 927, BONUS: 0.5,
  BAND_M: 110,                // 작업 경로를 짤 때 나누는 띠 폭(m)
};

// ---------------------------------------------------------------- 실측 단지 불러오기
// kind: member(단지 안 참여 필지) · cand(주변 논, 편입 후보) · bg(밭·시설·다른 단지 등 배경)
function loadField(D) {
  const parcels = D.parcels.map((q) => ({
    id: q.id, pnu: q.pnu, use: q.use, area: q.area, soil: q.soil, drain: q.drain,
    kind: q.kind, member: q.kind === "member", bg: q.kind === "bg", owner: q.owner,
    c: q.c, poly: q.poly, nb: q.nb,
  }));
  const farms = new Map();
  parcels.forEach((p) => {
    if (!p.owner) return;
    if (!farms.has(p.owner)) farms.set(p.owner, { name: p.owner, member: p.member, parcels: [], area: 0 });
    const f = farms.get(p.owner); f.parcels.push(p.id); f.area += p.area;
  });
  return { parcels, farms, depot: D.depot, roads: D.roads, meta: D.meta };
}

// ---------------------------------------------------------------- 기하
const cx = (p) => p.c[0], cy = (p) => p.c[1];
const neighbors = (p) => p.nb;
const areaOf = (ids) => { let a = 0; for (const id of ids) a += F.parcels[id].area; return a; };
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
// 작업 경로: 보관소에서 출발해, 단지를 동서로 일정 폭의 띠로 나누고 띠마다 남북으로 지그재그로 돌며
// 모든 필지를 지나고 돌아온다. 구멍은 건너뛰고, 편입한 땅은 경로에 더해진다. 대안마다 경로가 달라진다.
function coverPath(set) {
  const bands = new Map();
  for (const id of set) { const p = F.parcels[id], b = Math.floor(cx(p) / P.BAND_M); if (!bands.has(b)) bands.set(b, []); bands.get(b).push(p); }
  let keys = [...bands.keys()].sort((a, b) => a - b);
  const mid = (keys[0] + keys[keys.length - 1]) / 2 * P.BAND_M;
  if (F.depot[0] > mid) keys = keys.reverse();          // 보관소에 가까운 쪽 띠부터
  const path = [F.depot];
  keys.forEach((b, i) => {
    const list = bands.get(b).sort((p, q) => (i % 2 ? cy(q) - cy(p) : cy(p) - cy(q)));
    list.forEach((p) => path.push([cx(p), cy(p)]));
  });
  path.push(F.depot);
  let len = 0; for (let i = 1; i < path.length; i++) len += dist(path[i - 1], path[i]);
  return { path, km: len / 1000 };
}

// ---------------------------------------------------------------- 평가
function evaluate(set, extra = {}) {
  const area = areaOf(set);
  const comps = components(set);
  const t = coverPath(set);
  let edges = 0;
  for (const id of set) for (const nb of neighbors(F.parcels[id])) if (!set.has(nb)) edges++;
  const setupH = comps.length * P.OPS * P.SETUP_H;
  const travelH = t.km * P.OPS / P.SPEED_KMH;
  const turnH = edges * P.OPS * P.TURN_H;
  const fieldH = area * P.OPS * 0.4;
  const machineWon = (setupH + travelH + turnH) * P.MACH_WON_H;
  const pyeong = area * P.PY_PER_HA;
  // 법인 순수익(원/년)과, 면적 비례로 나눌 때의 평당 배당
  const net = {}, per = {};
  for (const k of ["lo", "mid", "hi"]) { net[k] = area * (P.REV[k] - P.COST) * 1e4 - machineWon; per[k] = pyeong ? net[k] / pyeong : 0; }
  const farmSet = new Set([...set].map((id) => F.parcels[id].owner));
  const memberFarms = [...farmSet].filter((f) => F.farms.get(f).member || (extra.added || []).includes(f));
  return {
    set, area, blocks: comps.length, routeKm: t.km, path: t.path, edges,
    hours: setupH + travelH + turnH + fieldH, machineWon, per, pyeong,
    farms: memberFarms.length, net,
  };
}
// 목표 판정은 법인 순수익(1차 고객인 법인의 질문)으로 한다. target은 원 단위.
function verdict(net, target) { return net.lo >= target ? "safe" : net.mid >= target ? "ok" : "bad"; }
const VERD = { safe: ["안전", "safe"], ok: ["가능", "ok"], bad: ["미달", "bad"] };
const RANK = { safe: 0, ok: 1, bad: 2 };

// ---------------------------------------------------------------- 상태
// off: 해제(거절)된 참여 필지 id. unit: 지도 클릭 단위(farm = 농가 전체, parcel = 필지 한 칸)
const S = { off: new Set(), unit: "farm", areaMin: 20, farmMin: 5, target: null, pay: "div", shock: 20, sel: null, tab: 1, hover: null };
const refusedFarms = () => new Set([...S.off].map((id) => F.parcels[id].owner));
let F = null; // init()에서 window.EF_DATA로 채운다
let VIEW = null; // 마지막 계산 결과

function memberParcels() { return F.parcels.filter((p) => p.member).map((p) => p.id); }

function buildOptions() {
  const base = new Set(memberParcels().filter((id) => !S.off.has(id)));
  const holes = memberParcels().filter((id) => S.off.has(id));
  const nRefused = refusedFarms().size;
  const opts = [];
  const mk = (key, name, sub, set, persuade, meta = {}) => {
    const ev = evaluate(set, meta);
    const meets = ev.area + 1e-9 >= S.areaMin && ev.farms >= S.farmMin;
    const why = [];
    if (ev.area + 1e-9 < S.areaMin) why.push(`면적 ${(S.areaMin - ev.area).toFixed(1)}ha 부족`);
    if (ev.farms < S.farmMin) why.push(`농가 ${S.farmMin - ev.farms}명 부족`);
    opts.push({ key, name, sub, persuade, meets, why: why.join(" · "), v: verdict(ev.net, S.target), ...meta, ev });
  };

  if (!S.off.size) {
    mk("base", "기본 필지화", "전원 참여 · 구멍 없음", base, 0);
    return { opts, base, holes };
  }
  mk("keep", "그대로", "땅 추가 없이 경로만 다시 짬", base, 0);

  // 경작권 교환: 구멍 필지를 법인이 짓고, 면적이 비슷한 가장자리 필지를 거절 농가에 맡긴다
  const holeSet = new Set(holes), holeArea = areaOf(holes);
  const nearHole = (id) => neighbors(F.parcels[id]).some((nb) => holeSet.has(nb));
  const coopNb = (id) => neighbors(F.parcels[id]).filter((nb) => base.has(nb)).length;
  // 가장자리 = 단지 안 이웃이 적고 중심에서 먼 필지. 구멍 바로 옆 필지는 넘기지 않는다.
  const edgeCands = [...base].filter((id) => !nearHole(id))
    .sort((a, b) => coopNb(a) - coopNb(b) || distFromCenter(b) - distFromCenter(a));
  let exSet = null, exGive = [], given = 0;
  for (const id of edgeCands) { if (given >= holeArea - 0.05) break; exGive.push(id); given += F.parcels[id].area; }
  if (given >= holeArea - 0.05) {
    exSet = new Set([...base].filter((id) => !exGive.includes(id)).concat(holes));
    mk("ex", "경작권 교환", `구멍 ${holes.length}필지(${holeArea.toFixed(1)}ha) ↔ 가장자리 ${exGive.length}필지(${given.toFixed(1)}ha)`, exSet, nRefused, { exIn: holes, exOut: exGive });
  }

  // 땅 추가: 단지에 닿아 있는 주변 논(추정 비참여 농가)을 농가 단위로 편입. 밭·시설·다른 단지는 제외
  const touching = new Set();
  for (const id of base) for (const nb of neighbors(F.parcels[id])) { const p = F.parcels[nb]; if (!p.member && !p.bg && p.owner) touching.add(p.owner); }
  const cand = [...touching].map((f) => {
    const ids = F.farms.get(f).parcels;
    const set = new Set([...base, ...ids]);
    return { f, ids, ev: evaluate(set, { added: [f] }) };
  }).sort((a, b) => a.ev.blocks - b.ev.blocks || b.ev.area - a.ev.area || b.ev.per.mid - a.ev.per.mid);
  const top = cand.slice(0, 4);
  top.forEach((c) => mk(`add:${c.f}`, `${c.f} 편입`, `+${areaOf(c.ids).toFixed(1)}ha · 경로 다시 짬`, new Set([...base, ...c.ids]), 1, { added: [c.f], addIds: c.ids }));
  for (let i = 0; i < Math.min(3, top.length); i++) for (let j = i + 1; j < Math.min(3, top.length); j++) {
    const a = top[i], b = top[j], ids = [...a.ids, ...b.ids];
    mk(`add2:${a.f}+${b.f}`, `${a.f} + ${b.f} 편입`, `+${areaOf(ids).toFixed(1)}ha`, new Set([...base, ...ids]), 2, { added: [a.f, b.f], addIds: ids });
  }
  if (exSet && top.length) {
    const c = top[0];
    mk(`exadd:${c.f}`, `교환 + ${c.f} 편입`, `구멍 메움 + ${areaOf(c.ids).toFixed(1)}ha`, new Set([...exSet, ...c.ids]), nRefused + 1, { exIn: holes, exOut: exGive, added: [c.f], addIds: c.ids });
  }
  return { opts, base, holes, candFarms: top.map((c) => c.f) };
}
let CENTER = null;
function distFromCenter(id) {
  if (!CENTER) { const ids = memberParcels(); CENTER = [ids.reduce((s, i) => s + cx(F.parcels[i]), 0) / ids.length, ids.reduce((s, i) => s + cy(F.parcels[i]), 0) / ids.length]; }
  const p = F.parcels[id]; return dist([cx(p), cy(p)], CENTER);
}

function rankOptions(opts) {
  const ok = opts.filter((o) => o.meets).sort((a, b) => RANK[a.v] - RANK[b.v] || a.persuade - b.persuade || b.ev.net.mid - a.ev.net.mid);
  const no = opts.filter((o) => !o.meets).sort((a, b) => b.ev.area - a.ev.area);
  ok.forEach((o, i) => { o.rank = i + 1; });
  return ok.concat(no);
}

// ---------------------------------------------------------------- 렌더링 도우미
const $ = (id) => document.getElementById(id);
const won = (v) => `${Math.round(v).toLocaleString("ko-KR")}원`;
const man = (v) => `${Math.round(v / 1e4).toLocaleString("ko-KR")}만 원`;
const eokS = (v) => `${(v / 1e8).toFixed(2)}억`;
// 큰 금액: 1억 5,900만 원
const krw = (v) => {
  const sign = v < 0 ? "−" : "", a = Math.abs(v), eok = Math.floor(a / 1e8), m = Math.round((a - eok * 1e8) / 1e4);
  return sign + (eok ? `${eok}억${m ? ` ${m.toLocaleString("ko-KR")}만` : ""} 원` : `${m.toLocaleString("ko-KR")}만 원`);
};
const ha = (v) => `${v.toFixed(1)}ha`;
const chip = (v) => `<span class="chip ${VERD[v][1]}">${VERD[v][0]}</span>`;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const SVGNS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, parent) { const e = document.createElementNS(SVGNS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }

// ---------------------------------------------------------------- 메인 계산·그리기
function recompute() {
  const t0 = performance.now();
  CENTER = null;
  const full = evaluate(new Set(memberParcels()));
  // 기본 목표 = 전원 참여 때의 순수익(원래 계획), 100만 원 단위로 내림
  const defTarget = Math.floor(full.net.mid / 1e6) * 1e6;
  if (S.target === null) {
    S.target = defTarget;
    $("inTarget").value = Math.round(S.target / 1e4);
  }
  const { opts, holes, candFarms } = buildOptions();
  const ranked = rankOptions(opts);
  const keep = opts.find((o) => o.key === "keep" || o.key === "base");
  const stage = !S.off.size ? 0 : keep.meets ? 1 : 2;
  if (!ranked.some((o) => o.key === S.sel)) S.sel = (ranked.find((o) => o.rank === 1) || ranked[0]).key;
  const sel = ranked.find((o) => o.key === S.sel);
  VIEW = { ranked, sel, stage, holes, candFarms: candFarms || [], keep, full, defTarget, ms: performance.now() - t0 };
  renderStepper(); renderDecide(); renderRefused(); renderJudge(); renderMap(); renderOptions(); renderMachine(); renderPayout();
  if ($("sheetDlg").open) renderSheet();
  writeHash();
}

// ---------------------------------------------------------------- 주소에 화면 상태 담기
// 사무장이 링크 하나로 대표에게 같은 화면(거절 필지 · 조건 · 선택한 안)을 보낼 수 있게 한다.
// 예: empty-field.html#off=12,13,40&area=50&pay=rent  (기본값인 항목은 적지 않는다)
const pressSeg = (id, v) => $(id).querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.v === String(v))));
function readHash() {
  const q = new URLSearchParams(location.hash.slice(1));
  S.off = new Set((q.get("off") || "").split(",").map(Number).filter((id) => Number.isInteger(id) && F.parcels[id]?.member));
  S.areaMin = q.get("area") === "50" ? 50 : 20; pressSeg("segArea", S.areaMin);
  S.pay = ["div", "rent", "mix"].includes(q.get("pay")) ? q.get("pay") : "div"; pressSeg("segPay", S.pay);
  const fm = Number(q.get("farm")); S.farmMin = fm >= 1 && fm <= 20 ? fm : 5; $("inFarmMin").value = S.farmMin;
  const tg = Number(q.get("target")); S.target = q.has("target") && tg >= 0 ? tg * 1e4 : null;
  if (S.target !== null) $("inTarget").value = tg;
  S.sel = q.get("sel");
}
function writeHash() {
  const q = new URLSearchParams();
  if (S.off.size) q.set("off", [...S.off].join(","));
  if (S.areaMin !== 20) q.set("area", S.areaMin);
  if (S.farmMin !== 5) q.set("farm", S.farmMin);
  if (S.target !== VIEW.defTarget) q.set("target", Math.round(S.target / 1e4));
  if (S.pay !== "div") q.set("pay", S.pay);
  if (S.off.size && S.sel !== VIEW.ranked.find((o) => o.rank === 1)?.key) q.set("sel", S.sel);
  const h = q.toString().replace(/%2C/g, ","), url = location.pathname + location.search + (h ? `#${h}` : "");
  if (url !== location.pathname + location.search + location.hash) history.replaceState(null, "", url);
}
// 조건을 바꿨을 때 다시 계산된 영역을 잠깐 강조해, 바뀐 것을 눈으로 알 수 있게 한다
function flash(note) {
  ["decide", "judge", "optTable", "machine", "payout"].forEach((id) => {
    const n = $(id); if (!n) return;
    n.classList.remove("flash"); void n.offsetWidth; n.classList.add("flash");
  });
  if (note) { const s = $("recalc"); s.textContent = `${note} → 다시 계산했습니다`; s.classList.remove("flash"); void s.offsetWidth; s.classList.add("flash"); }
}

function renderStepper() {
  document.querySelectorAll("#stepper li").forEach((li) => {
    const on = Number(li.dataset.stage) === VIEW.stage;
    li.classList.toggle("on", on);
    if (on) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  });
}

// 결론: 거절 당일 사무장이 먼저 묻는 세 가지(지원 기준 유지 · 순수익과 배당 영향 · 다음 할 일)를 맨 위에 둔다
const pct = (a, b) => (b ? (a / b - 1) * 100 : 0);
const signPct = (v) => (Math.abs(v) < 0.05 ? "0.0%" : `${v > 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}%`);
const STAGE = ["① 기본 필지화", "② 구멍 · 기준 충족", "③ 구멍 · 기준 미충족"];
function renderDecide() {
  const { stage, keep, holes, full, ranked } = VIEW, e = keep.ev;
  const best = ranked.find((o) => o.rank === 1);
  const goalGap = S.target - e.net.mid;
  const head = stage === 0
    ? (keep.meets ? "지도에서 거절한 농가를 누르면, 빼고 갈지 메울지 바로 판정합니다" : `전원 참여해도 지원 기준에 못 미칩니다 (${esc(keep.why)})`)
    : stage === 1
      ? (goalGap > 0 ? `빼고 가도 지원 기준은 지킵니다. 다만 목표 순수익에 ${krw(goalGap)} 모자랍니다` : "빼고 가도 됩니다. 지원 기준과 목표 순수익을 모두 지킵니다")
      : `메워야 합니다. 그대로면 ${esc(keep.why)}으로 지원 대상에서 빠집니다`;
  const sec = `판정 ${Math.max(0.01, VIEW.ms / 1000).toFixed(2)}초`;
  const sub = stage === 0
    ? `참여 ${e.farms}농가 · ${ha(e.area)} · 이 상태가 모든 비교의 기준선입니다 · ${sec}`
    : `거절 ${refusedFarms().size}농가 · ${holes.length}필지 · ${ha(areaOf(holes))} 빠짐 · ${sec}`;
  const areaOk = e.area + 1e-9 >= S.areaMin, farmOk = e.farms >= S.farmMin;
  const dNet = pct(e.net.mid, full.net.mid), dPer = pct(e.per.mid, full.per.mid);
  const perNote = stage === 0 ? "면적대로 나눈 연 배당 · 농가 설득용"
    : Math.abs(dPer) < 2 ? `전원 참여 대비 ${signPct(dPer)} · 땅과 받을 사람이 함께 빠져 거의 그대로`
      : `전원 참여 대비 ${signPct(dPer)}`;
  let next;
  if (stage === 0) next = `<div class="n sm">거절이 나오면 여기에 1순위 대안이 뜹니다</div>`;
  else if (!best) next = `<div class="n sm fail">기준을 넘는 대안이 없습니다</div><div class="d">주변 농가를 더 설득하거나 지원 기준을 다시 확인하세요</div>`;
  else if (best.key === "keep") next = `<div class="n sm">그대로 진행 ${chip(best.v)}</div><div class="d">추가로 설득할 농가가 없습니다</div>`;
  else next = `<div class="n sm">1순위 ${esc(best.name)} ${chip(best.v)}</div><div class="d">${esc(best.sub)} · 설득 ${best.persuade}농가</div>
      <button type="button" class="btn small" data-pick="${esc(best.key)}">지도에서 보기</button>`;
  const d = $("decide");
  d.className = `decide s${stage}`;
  d.innerHTML = `
    <div class="verdict">
      <span class="stage-tag">${STAGE[stage]}</span>
      <p class="headline">${head}</p>
      <p class="d">${sub}</p>
    </div>
    <div class="tiles">
      <div class="tile ${areaOk && farmOk ? "ok" : "no"}">
        <div class="k">지원 기준${stage ? " · 그대로 갈 때" : ""}</div>
        <div class="n">${areaOk && farmOk ? "충족 ✓" : "미충족 ✗"}</div>
        <div class="d"><span class="${areaOk ? "pass" : "fail"}">면적 ${ha(e.area)} / ${S.areaMin}ha</span><br><span class="${farmOk ? "pass" : "fail"}">농가 ${e.farms}명 / ${S.farmMin}명</span></div>
      </div>
      <div class="tile">
        <div class="k">법인 순수익 (연)${stage ? " · 그대로 갈 때" : ""}</div>
        <div class="n">${krw(e.net.mid)}</div>
        <div class="d ${stage && dNet < 0 ? "fail" : ""}">${stage ? `전원 참여 대비 ${signPct(dNet)}` : `범위 ${eokS(e.net.lo)}~${eokS(e.net.hi)}원`}</div>
      </div>
      <div class="tile">
        <div class="k">남은 조합원 평당 배당 (연)</div>
        <div class="n">${won(e.per.mid)}</div>
        <div class="d">${perNote}</div>
      </div>
      <div class="tile next">
        <div class="k">다음 할 일</div>
        ${next}
      </div>
    </div>`;
}

function renderRefused() {
  // S.off는 넣은 순서를 지키므로 거절한 순서대로 보인다
  const list = [...refusedFarms()].map((f) => {
    const farm = F.farms.get(f), off = farm.parcels.filter((id) => S.off.has(id));
    return { f, n: off.length, total: farm.parcels.length, area: areaOf(off) };
  });
  $("refusedCount").textContent = list.length ? `· ${list.length}농가 ${ha(list.reduce((s, r) => s + r.area, 0))}` : "";
  $("refused").innerHTML = list.length
    ? list.map((r) => `<li><span><b>${esc(r.f)}</b> ${ha(r.area)}<span class="sub">${r.n === r.total ? `필지 ${r.n}개 전체` : `필지 ${r.total}개 중 ${r.n}개`}</span></span>
        <button type="button" class="btn small" data-restore="${esc(r.f)}" aria-label="${esc(r.f)} 참여로 복구">복구</button></li>`).join("")
    : `<li class="empty">아직 거절한 농가가 없습니다. 지도에서 초록 필지를 누르세요.</li>`;
  // 지도의 필지가 손가락보다 작은 휴대폰에서도 정확히 고르도록, 참여 농가를 이름으로도 고른다
  const members = [...F.farms.values()].filter((f) => f.member).sort((a, b) => a.name.localeCompare(b.name, "ko", { numeric: true }));
  $("farmPickList").innerHTML = members.map((f) => {
    const off = f.parcels.some((id) => S.off.has(id));
    return `<button type="button" class="pick" data-farm="${esc(f.name)}" aria-pressed="${off}">${esc(f.name)}<span class="sub">${off ? "거절 · " : ""}${ha(f.area)}</span></button>`;
  }).join("");
}
function toggleFarm(name) {
  const ids = F.farms.get(name).parcels, allOff = ids.every((id) => S.off.has(id));
  ids.forEach((id) => (allOff ? S.off.delete(id) : S.off.add(id)));
  S.sel = null; recompute();
}
const hlFarm = (name) => {
  const ids = new Set(name ? F.farms.get(name).parcels : []);
  document.querySelectorAll("#map [data-id]").forEach((x) => x.classList.toggle("hl", ids.has(Number(x.dataset.id))));
};

function renderJudge() {
  const o = VIEW.sel, e = o.ev;
  $("judgeOf").textContent = `· ${o.name}`;
  const areaPct = Math.min(100, e.area / Math.max(S.areaMin * 1.25, e.area) * 100);
  const minPct = S.areaMin / Math.max(S.areaMin * 1.25, e.area) * 100;
  const areaOk = e.area + 1e-9 >= S.areaMin, farmOk = e.farms >= S.farmMin, conn = e.blocks === 1;
  const ref = Math.max(S.target, VIEW.full.net.hi, 1);
  const lo = ref * 0.5, hi = ref * 1.1, pos = (v) => Math.max(0, Math.min(100, (v - lo) / (hi - lo) * 100));
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
    <h3 style="margin-top:12px">법인 순수익 (연) ${chip(o.v)}</h3>
    <div class="range" role="img" aria-label="법인 순수익 ${krw(e.net.lo)}에서 ${krw(e.net.hi)}, 기준 ${krw(e.net.mid)}, 목표 ${krw(S.target)}">
      <div class="track"></div>
      <div class="band" style="left:${pos(e.net.lo)}%;width:${pos(e.net.hi) - pos(e.net.lo)}%"></div>
      <div class="dot" style="left:${pos(e.net.mid)}%"></div>
      <div class="tgt" style="left:${pos(S.target)}%" title="목표"></div>
    </div>
    <div class="scale"><span>${eokS(e.net.lo)}원</span><span><b>기준 ${eokS(e.net.mid)}원</b></span><span>${eokS(e.net.hi)}원</span></div>
    <p class="hint">검은 선 = 목표 ${krw(S.target)} · 전원 참여 기준선 ${krw(VIEW.full.net.mid)}</p>
    <p class="hint">참고: 면적대로 나누면 평당 배당 ${won(e.per.mid)} (농가 설득용 지표, 3번 탭)</p>`;
}

// 단지 중심 보기: 참여 필지, 편입 후보가 될 수 있는 이웃 농가 전체, 보관소 이름표까지 담는 고정 범위.
// 거절할 때마다 범위가 바뀌면 누르던 자리가 움직이므로 처음 한 번만 정한다.
let ZOOM_BOX = null;
function zoomBox() {
  if (ZOOM_BOX) return ZOOM_BOX;
  const u = F.meta.width / 1000, members = F.parcels.filter((p) => p.member), touch = new Set();
  members.forEach((p) => p.nb.forEach((n) => { const q = F.parcels[n]; if (!q.member && !q.bg && q.owner) touch.add(q.owner); }));
  const pts = F.parcels.filter((p) => p.member || touch.has(p.owner)).flatMap((p) => p.poly);
  pts.push(F.depot);     // 이름표는 단지 중심 보기에서 보관소 왼쪽에 붙인다
  const pad = 20 * u, xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
  const x0 = Math.max(0, Math.min(...xs) - pad), y0 = Math.max(0, Math.min(...ys) - pad);
  const x1 = Math.min(F.meta.width, Math.max(...xs) + pad), y1 = Math.min(F.meta.height, Math.max(...ys) + pad);
  return (ZOOM_BOX = [x0, y0, x1 - x0, y1 - y0].map((v) => Math.round(v)));
}
function renderMap() {
  const svg = $("map"), { sel, holes, candFarms } = VIEW;
  const W = F.meta.width, H = F.meta.height, u = W / 1000;     // u: 화면 크기에 맞춘 글자·기호 단위
  svg.setAttribute("viewBox", (S.zoom ? zoomBox() : [0, 0, W, H]).join(" "));
  svg.innerHTML = "";
  // 도로(OpenStreetMap)
  const gr = el("g", { "pointer-events": "none" }, svg);
  F.roads.forEach((line) => el("polyline", { points: line.map((q) => q.join(",")).join(" "), fill: "none", stroke: "var(--road)", "stroke-width": 3, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round" }, gr));
  const exIn = new Set(sel.exIn || []), exOut = new Set(sel.exOut || []), add = new Set(sel.addIds || []);
  const candSet = new Set();
  candFarms.forEach((f) => F.farms.get(f).parcels.forEach((id) => candSet.add(id)));
  const holeSet = new Set(holes);
  const hl = S.hover !== null ? hoverGroup(S.hover) : new Set();
  // 거절 땅 빗금: 색을 구분하기 어려운 사람도 알아보게 한다
  const pat = el("pattern", { id: "hatchHole", patternUnits: "userSpaceOnUse", width: 9 * u, height: 9 * u, patternTransform: "rotate(45)" }, el("defs", {}, svg));
  el("rect", { width: 9 * u, height: 9 * u, fill: "var(--hole-soft)" }, pat);
  el("line", { x1: 0, y1: 0, x2: 0, y2: 9 * u, stroke: "var(--hole)", "stroke-width": 3 * u, opacity: 0.55 }, pat);
  const g = el("g", {}, svg), gl = el("g", { "pointer-events": "none" }, svg);
  F.parcels.forEach((p) => {
    // 색은 뜻으로 나눈다: 법인 단지 연두, 거절 빨강, 교환 파랑, 들어오는(편입) 땅 볏짚 노랑. ink는 지도 위 글자색
    let fill = "var(--other)", stroke = "var(--other-line)", ink = "", dash = "", cls = "parcel", label = "", sw = 1;
    if (p.bg) { fill = "var(--bg-parcel)"; stroke = "var(--bg-parcel-line)"; }
    if (p.member && !holeSet.has(p.id)) { fill = "var(--coop-soft)"; stroke = "var(--coop)"; cls += " member"; }
    if (holeSet.has(p.id)) { fill = "url(#hatchHole)"; stroke = "var(--hole)"; ink = "var(--hole-strong)"; cls += " member"; label = "거절"; sw = 2; }
    if (exIn.has(p.id)) { fill = "var(--ex-soft)"; stroke = "var(--ex)"; ink = "var(--ex-text)"; label = "교환"; sw = 2; }
    if (exOut.has(p.id)) { fill = "var(--card)"; stroke = "var(--ex)"; ink = "var(--ex-text)"; dash = "4 3"; label = "넘김"; sw = 2; }
    if (!p.member && candSet.has(p.id) && !add.has(p.id)) { fill = "var(--add-tint)"; stroke = "var(--add)"; dash = "4 3"; sw = 1.5; }
    if (add.has(p.id)) { fill = "var(--add-soft)"; stroke = "var(--add)"; ink = "var(--add-text)"; label = "편입"; sw = 2.5; }
    if (hl.has(p.id)) cls += " hl";
    const r = el("polygon", { points: p.poly.map((q) => q.join(",")).join(" "), fill, stroke, "stroke-width": sw, "vector-effect": "non-scaling-stroke", class: cls, "data-id": p.id }, g);
    if (dash) r.setAttribute("stroke-dasharray", dash);
    const off = S.off.has(p.id);
    if (p.member) {
      const f = F.farms.get(p.owner);
      const scope = S.unit === "farm" ? `${p.owner}의 필지 ${f.parcels.length}개(${f.area.toFixed(1)}ha) 전체` : `이 필지(${p.area.toFixed(2)}ha)`;
      // 농가 단위일 때는 농가마다 한 칸만 Tab에 걸어, 필지 133칸을 하나씩 지나지 않게 한다
      r.setAttribute("tabindex", S.unit === "parcel" || f.parcels[0] === p.id ? "0" : "-1"); r.setAttribute("role", "button");
      r.setAttribute("aria-label", `${p.owner} 필지 ${p.area.toFixed(2)}ha, ${off ? "거절됨" : "참여 중"}. 누르면 ${scope}를 ${off ? "복구" : "해제"}`);
    }
    const tt = el("title", {}, r);
    tt.textContent = p.member ? `${p.owner}(추정) · ${p.area.toFixed(2)}ha · ${off ? "거절" : "참여"}`
      : p.bg ? `${p.use} · 편입 대상 아님` : `${p.owner}(추정) · 비참여 논 ${p.area.toFixed(2)}ha`;
    if (label && p.area >= 0.12) { const t = el("text", { x: cx(p), y: cy(p) + 6 * u, "text-anchor": "middle", "font-size": 17 * u, "font-weight": 700, fill: ink || stroke }, gl); t.textContent = label; }
  });
  // 작업 경로
  const pts = sel.ev.path.map((q) => q.join(",")).join(" ");
  el("polyline", { points: pts, fill: "none", stroke: "var(--ink)", "stroke-width": 1.8, "vector-effect": "non-scaling-stroke", "stroke-dasharray": "6 4", "stroke-linejoin": "round", "pointer-events": "none", opacity: 0.85 }, svg);
  const [dx, dy] = F.depot, ds = 22 * u;
  el("rect", { x: dx - ds / 2, y: dy - ds / 2, width: ds, height: ds, fill: "var(--ink)", "pointer-events": "none" }, svg);
  const t = el("text", { x: S.zoom ? dx - ds : dx + ds, "text-anchor": S.zoom ? "end" : "start", y: dy + 7 * u, "font-size": 20 * u, fill: "var(--ink)", "font-weight": 600, "pointer-events": "none", "paint-order": "stroke", stroke: "var(--card)", "stroke-width": 4 * u }, svg); t.textContent = "농기계 보관소";

  const e = sel.ev;
  const dKm = (e.routeKm - VIEW.full.routeKm) * P.OPS;
  $("mapNote").innerHTML = `<span class="stage-tag s${VIEW.stage}">${STAGE[VIEW.stage]}</span> 선택한 안: <b>${esc(sel.name)}</b> · ${ha(e.area)} · 블록 ${e.blocks}개 · 트랙터 이동 연 ${(e.routeKm * P.OPS).toFixed(1)}km <span class="muted">(전원 참여 대비 ${dKm >= 0 ? "+" : "−"}${Math.abs(dKm).toFixed(1)}km)</span>`;
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
      <td class="num" data-label="면적">${e.area.toFixed(1)}<span class="unit-m">ha</span></td>
      <td class="num" data-label="설득">${o.persuade}명</td>
      <td class="num" data-label="순수익(연)" title="${krw(e.net.mid)}"><b>${eokS(e.net.mid)}</b><span class="sub">${(e.net.lo / 1e8).toFixed(2)}~${eokS(e.net.hi)}</span><span class="sub">평당 ${won(e.per.mid)}</span></td>
      <td>${o.meets ? chip(o.v) : `<span class="chip na">기준 미달</span>`}</td>
    </tr>`;
  }).join("");
  $("optTable").innerHTML = `<table aria-label="대안 비교">
    <thead><tr><th>순위</th><th>대안</th><th class="num">ha</th><th class="num">설득</th><th class="num">순수익(연)</th><th>판정</th></tr></thead>
    <tbody>${rows}</tbody></table>
    ${hiddenN || S.showAll ? `<button type="button" class="btn more" id="btnMore" aria-expanded="${!!S.showAll}">${S.showAll ? "▴ 상위 5개만 보기" : `▾ 나머지 대안 ${hiddenN}개 더 보기`}</button>` : ""}
    <p class="hint">행을 누르면 지도와 왼쪽 판정이 그 안으로 바뀝니다. 순수익(억 원) 아래 첫 줄은 보수~낙관 범위, 둘째 줄은 면적대로 나눈 평당 배당입니다. 금액에 마우스를 올리면 만 원 단위로 보입니다.</p>`;
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
// 평당 금액: p0 평년 기준값, p1 흉년(S.shock), pLo·pHi 6년 최저·최고
function payoutNums(e) {
  const s = S.shock / 100;
  const perShock = (e.area * (P.REV.mid * (1 - s) - P.COST) * 1e4 - e.machineWon) / (e.area * P.PY_PER_HA);
  return { p0: payFor(e.per.mid), p1: payFor(perShock), pLo: payFor(e.per.lo), pHi: payFor(e.per.hi) };
}
const PAY_NAMES = { div: "배당형", rent: "고정 임차료", mix: "혼합" };
// 흉년 손실을 누가 지는지 (배당형은 조합원, 고정 임차료형은 법인)
function shockSentence(e, p0, p1) {
  if (!S.shock) return "작황을 낮추면 흉년 손실을 누가 지는지 보입니다.";
  const coop = p1.rent.coop * e.pyeong;
  return `흉년(−${S.shock}%)이면 <b>배당형</b>은 조합원이 평당 ${won(p0.div.member - p1.div.member)} 덜 받습니다. <b>고정 임차료형</b>은 조합원 몫은 그대로이고, 법인 유보가 연 ${coop >= 0 ? `${krw(coop)} 남습니다` : `${krw(-coop)} 적자입니다`}.`;
}
function renderPayout() {
  const e = VIEW.sel.ev;
  $("shockVal").textContent = S.shock ? `평년 대비 −${S.shock}%` : "평년";
  const { p0, p1, pLo, pHi } = payoutNums(e);
  const names = PAY_NAMES;
  const row = (k) => `<tr ${k === S.pay ? 'class="sel"' : ""}><td>${names[k]}</td>
      <td><b>${won(p0[k].member)}</b><span class="sub">${Math.round(pLo[k].member) === Math.round(pHi[k].member) ? "고정" : `${Math.round(pLo[k].member).toLocaleString("ko-KR")}~${Math.round(pHi[k].member).toLocaleString("ko-KR")}`}</span></td>
      <td class="${p1[k].member < p0[k].member - 0.5 ? "neg" : ""}"><b>${won(p1[k].member)}</b></td>
      <td class="${p1[k].coop < 0 ? "neg" : "pos"}">${p1[k].coop >= 0 ? "+" : ""}${won(p1[k].coop)}</td></tr>`;
  // 농가별 배당금
  const byFarm = new Map();
  for (const id of e.set) { const p = F.parcels[id]; byFarm.set(p.owner, (byFarm.get(p.owner) || 0) + p.area); }
  const farmRows = [...byFarm.entries()].sort((a, b) => b[1] - a[1]).map(([f, n]) => {
    const pyeong = n * P.PY_PER_HA;
    return `<tr><td>${esc(f)}</td><td>${n.toFixed(2)}</td><td>${man(pyeong * p0[S.pay].member)}</td><td class="${p1[S.pay].member < p0[S.pay].member - 0.5 ? "neg" : ""}">${man(pyeong * p1[S.pay].member)}</td></tr>`;
  }).join("");
  const shockLabel = S.shock ? `흉년 −${S.shock}%` : "평년";
  $("payout").innerHTML = `
    <p class="lead">${shockSentence(e, p0, p1)}</p>
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

// ---------------------------------------------------------------- 설명 자료 (인쇄용 한 장)
// 사무장이 쓰고, 대표가 정하고, 남은 농가를 설득한다. 대표에게는 결정할 거리를, 농가에게는 내 땅 기준 금액을 준다.
function renderSheet() {
  const kind = S.sheet || "boss";
  $("sheet").className = `sheet ${kind}`;
  $("sheet").innerHTML = kind === "boss" ? sheetBoss() : sheetFarm();
}
const today = () => new Date().toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric" });
const sheetMeta = () => `포항 흥해 들녘 · 후보단지 ${esc(F.meta.complex_id)} (${esc(F.meta.complex_area_ha)}ha)`;

function sheetBoss() {
  const { stage, keep, full, ranked, holes } = VIEW, e = keep.ev;
  const refused = [...refusedFarms()];
  const best = ranked.find((o) => o.rank === 1);
  const rows = [...new Map([keep, ...ranked.filter((o) => o.rank).slice(0, 3)].map((o) => [o.key, o])).values()];
  const optRow = (o) => `<tr><td>${o.rank ? `${o.rank}순위 ` : ""}${esc(o.name)}<span class="sub">${esc(o.sub)}</span></td>
      <td class="num">${ha(o.ev.area)}</td><td class="num">${o.ev.farms}명</td><td class="num">${o.persuade}농가</td>
      <td class="num"><b>${eokS(o.ev.net.mid)}원</b><span class="sub">${eokS(o.ev.net.lo)}~${eokS(o.ev.net.hi)}</span></td>
      <td>${o.meets ? VERD[o.v][0] : `기준 미달 (${esc(o.why)})`}</td></tr>`;
  let rec;
  if (stage === 0) rec = "아직 거절이 없습니다. 거절이 나오면 이 자료를 다시 뽑아 보고합니다.";
  else if (!best) rec = "지원 기준을 넘는 대안이 없습니다. 주변 농가를 더 설득하거나, 지원 기준(면적·농가 수)을 다시 확인해야 합니다.";
  else if (best.key === "keep") rec = `<b>그대로 진행</b>을 권합니다. 거절 농가를 빼도 지원 기준을 지키고, 법인 순수익 ${krw(best.ev.net.mid)}로 목표 판정 '${VERD[best.v][0]}'입니다.`;
  else {
    const who = best.added ? `${best.added.join(", ")} 농가 편입 설득` : "";
    const ex = best.exIn ? "거절 농가와 경작권 교환 협의" : "";
    rec = `<b>${esc(best.name)}</b>을 권합니다. ${esc(best.sub)}. 설득할 농가 ${best.persuade}곳, 법인 순수익 ${krw(best.ev.net.mid)}(범위 ${eokS(best.ev.net.lo)}~${eokS(best.ev.net.hi)}원), 목표 판정 '${VERD[best.v][0]}'.${best.v === "bad" ? ` 목표 순수익에 ${krw(S.target - best.ev.net.mid)} 모자라므로, 목표를 다시 정하거나 추가 편입을 함께 검토해야 합니다.` : ""}
      <br>다음 할 일: ${esc([ex, who].filter(Boolean).join(" · "))}`;
  }
  return `
    <header class="sh-head"><p class="kicker">대표 보고 · ${today()}</p><h1>참여 거절 대응안</h1><p class="muted">${sheetMeta()} · 목표 순수익 ${krw(S.target)} · 지원 기준 ${S.areaMin}ha · ${S.farmMin}명</p></header>
    <section><h2>1. 상황 — ${STAGE[stage]}</h2>
      <p>${stage ? `거절 ${refused.length}농가(${esc(refused.join(", "))}) · ${holes.length}필지 · ${ha(areaOf(holes))}가 빠졌습니다.` : "전원 참여 상태입니다."}</p></section>
    <section><h2>2. 그대로 갈 때</h2>
      <table><tbody>
        <tr><th>경작 면적</th><td>${ha(e.area)} (기준 ${S.areaMin}ha) — ${e.area + 1e-9 >= S.areaMin ? "충족" : "미충족"}</td></tr>
        <tr><th>참여 농가</th><td>${e.farms}명 (기준 ${S.farmMin}명) — ${e.farms >= S.farmMin ? "충족" : "미충족"}</td></tr>
        <tr><th>법인 순수익 (연)</th><td>${krw(e.net.mid)} · 범위 ${eokS(e.net.lo)}~${eokS(e.net.hi)}원${stage ? ` · 전원 참여 대비 ${signPct(pct(e.net.mid, full.net.mid))}` : ""}</td></tr>
        <tr><th>남은 조합원 평당 배당</th><td>${won(e.per.mid)}${stage ? ` · 전원 참여 대비 ${signPct(pct(e.per.mid, full.per.mid))}` : ""}</td></tr>
      </tbody></table></section>
    ${stage ? `<section><h2>3. 대안 비교</h2>
      <table><thead><tr><th>대안</th><th class="num">면적</th><th class="num">농가</th><th class="num">설득</th><th class="num">순수익(연)</th><th>판정</th></tr></thead>
      <tbody>${rows.map(optRow).join("")}</tbody></table></section>` : ""}
    <section><h2>${stage ? 4 : 3}. 권고</h2><p>${rec}</p></section>
    <section><h2>${stage ? 5 : 4}. 결정할 일</h2>
      <ul class="checks"><li>권고안으로 진행할지</li><li>설득·교환 협의 담당자와 기한</li><li>남은 농가 설명 일정 (농가 설명용 자료 첨부)</li></ul></section>
    <footer class="sh-foot">순수익은 국가데이터처 논벼 생산비조사(2020~2025년산) 10a당 총수입 최저·평균·최고로 계산한 추정치입니다. 소유자는 추정값이며, 기계비·법인 운영비는 가정값입니다.</footer>`;
}

function sheetFarm() {
  const { stage, full, sel } = VIEW, e = sel.ev;
  const { p0, p1, pLo, pHi } = payoutNums(e);
  const dPer = pct(e.per.mid, full.per.mid);
  const shockLabel = S.shock ? `흉년 (수확 −${S.shock}%)` : "평년";
  const who = { div: "조합원", rent: "법인", mix: "나눠서 짐" };
  const lead = !stage ? `전원 참여하면 평당 배당은 보통 해 <b>${won(e.per.mid)}</b>입니다.`
    : `이웃 농가 일부가 빠졌지만, 남은 분들이 평당 받는 배당은 <b class="nw">${won(e.per.mid)}</b>으로 전원 참여 때(<span class="nw">${won(full.per.mid)}</span>)와 ${Math.abs(dPer) < 2
      ? `거의 같습니다 (<span class="nw">${signPct(dPer)}</span>). 빠진 땅만큼 법인 수입도, 나눌 면적도 함께 줄기 때문입니다.`
      : `비교해 ${signPct(dPer)} ${dPer > 0 ? "늘어납니다" : "줄어듭니다"}.`}`;
  const range = (k) => Math.round(pLo[k].member) === Math.round(pHi[k].member) ? "고정" : `${Math.round(pLo[k].member).toLocaleString("ko-KR")}~${Math.round(pHi[k].member).toLocaleString("ko-KR")}원`;
  const row = (k) => `<tr ${k === S.pay ? 'class="sel"' : ""}><td>${PAY_NAMES[k]}</td><td class="num"><b>${won(p0[k].member)}</b></td><td class="num">${range(k)}</td><td class="num">${won(p1[k].member)}</td><td>${who[k]}</td></tr>`;
  const mine = [1000, 2000, 3000, 5000].map((py) => `<tr><td>${py.toLocaleString("ko-KR")}평 <span class="muted">(${(py / P.PY_PER_HA).toFixed(2)}ha)</span></td>
      <td class="num"><b>${man(py * p0[S.pay].member)}</b></td><td class="num">${man(py * p1[S.pay].member)}</td></tr>`).join("");
  return `
    <header class="sh-head"><p class="kicker">참여 농가 안내 · ${today()}</p><h1>공동영농 배당 안내</h1><p class="muted">${sheetMeta()}</p></header>
    <p class="lead">${lead}</p>
    <section><h2>평당 받는 돈 (1년)</h2>
      <table><thead><tr><th>지급 방식</th><th class="num">보통 해</th><th class="num">최근 6년 범위</th><th class="num">${shockLabel}</th><th>흉년 손해는</th></tr></thead>
      <tbody>${["div", "rent", "mix"].map(row).join("")}</tbody></table></section>
    <section><h2>내 땅이면 1년에 얼마 (${PAY_NAMES[S.pay]})</h2>
      <table><thead><tr><th>내 땅</th><th class="num">보통 해</th><th class="num">${shockLabel}</th></tr></thead><tbody>${mine}</tbody></table></section>
    <p class="note">이 금액은 최근 6년(2020~2025년산) 논벼 수입으로 계산한 <b>추정치</b>이며, 약속된 금액이 아닙니다. 재해보험과 직불금은 빼고 계산했습니다.</p>
    <p class="contact">궁금한 점은 법인 사무장에게 물어 주세요. 연락처 ______________________</p>`;
}

// ---------------------------------------------------------------- 이벤트
// 마우스를 올렸을 때 함께 선택될 필지: 농가 단위면 그 농가의 참여 필지 전체, 필지 단위면 한 칸
function hoverGroup(id) {
  const p = F.parcels[id];
  if (!p.member) return new Set([id]);
  return S.unit === "farm" ? new Set(F.farms.get(p.owner).parcels) : new Set([id]);
}
function toggleParcel(id) {
  const p = F.parcels[id];
  if (!p.member) return;
  const ids = [...hoverGroup(id)];
  const allOff = ids.every((i) => S.off.has(i));
  ids.forEach((i) => (allOff ? S.off.delete(i) : S.off.add(i)));
  S.sel = null;
  recompute();
}
function bindSeg(id, key, conv, label) {
  $(id).addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    $(id).querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    S[key] = conv(b.dataset.v); recompute(); if (label) flash(`${label}: ${b.textContent}`);
  });
}
function setHover(id) {
  if (id === S.hover) return;
  S.hover = id;
  const grp = id === null ? new Set() : hoverGroup(id);
  document.querySelectorAll("#map [data-id]").forEach((x) => x.classList.toggle("hl", grp.has(Number(x.dataset.id))));
  const p = id === null ? null : F.parcels[id];
  $("hoverInfo").textContent = !p ? "" : p.member
    ? (S.unit === "farm" ? `${p.owner} · 필지 ${grp.size}개가 함께 선택됩니다` : `${p.owner}의 필지 한 칸`)
    : p.bg ? `${p.use} · 편입 대상 아님` : `${p.owner} · 비참여 (${F.farms.get(p.owner).area.toFixed(1)}ha) · 편입 후보`;
}
function selectTab(n) {
  S.tab = n;
  [1, 2, 3].forEach((i) => { $(`tab${i}`).setAttribute("aria-selected", String(i === n)); $(`pane${i}`).hidden = i !== n; });
}
function init() {
  if (!window.EF_DATA) {
    document.querySelector(".layout").innerHTML = `<p class="card">실측 단지 데이터(data/empty-field-heunghae.js)를 찾지 못했습니다. <code>python -m server.realdata.empty_field</code>로 만든 뒤 다시 여세요.</p>`;
    return;
  }
  F = loadField(window.EF_DATA);
  $("mapMeta").textContent = `포항 흥해 실측 필지 · 후보단지 ${F.meta.complex_id} (${F.meta.complex_area_ha}ha) · 소유자 추정`;
  $("map").addEventListener("click", (ev) => { const r = ev.target.closest("[data-id]"); if (r) toggleParcel(Number(r.dataset.id)); });
  $("map").addEventListener("keydown", (ev) => { if ((ev.key === "Enter" || ev.key === " ") && ev.target.dataset && ev.target.dataset.id) { ev.preventDefault(); const id = Number(ev.target.dataset.id); toggleParcel(id); document.querySelector(`#map [data-id="${id}"]`)?.focus(); } });
  $("map").addEventListener("mouseover", (ev) => { const r = ev.target.closest("[data-id]"); setHover(r ? Number(r.dataset.id) : null); });
  $("map").addEventListener("focusin", (ev) => { const r = ev.target.closest("[data-id]"); if (r) setHover(Number(r.dataset.id)); });
  $("map").addEventListener("mouseleave", () => setHover(null));
  $("segUnit").addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    $("segUnit").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    S.unit = b.dataset.v; S.hover = null; renderMap();
  });
  // 휴대폰에서는 필지가 손가락보다 작아지므로 단지 중심으로 크게 연다
  const setZoom = (on) => { S.zoom = on; $("segZoom").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String((x.dataset.v === "1") === on))); };
  setZoom(matchMedia("(max-width: 760px)").matches);
  $("segZoom").addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    setZoom(b.dataset.v === "1"); renderMap();
  });
  $("optTable").addEventListener("click", (ev) => {
    if (ev.target.closest("#btnMore")) { S.showAll = !S.showAll; renderOptions(); $("btnMore")?.focus(); return; }
    const tr = ev.target.closest("tr[data-key]"); if (tr) { S.sel = tr.dataset.key; recompute(); }
  });
  $("optTable").addEventListener("keydown", (ev) => { const tr = ev.target.closest("tr[data-key]"); if (tr && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); S.sel = tr.dataset.key; recompute(); document.querySelector(`tr[data-key="${CSS.escape(S.sel)}"]`)?.focus(); } });
  $("inTarget").addEventListener("input", (ev) => { S.target = (Number(ev.target.value) || 0) * 1e4; recompute(); flash(`목표 순수익 ${krw(S.target)}`); });
  $("inFarmMin").addEventListener("input", (ev) => { S.farmMin = Number(ev.target.value) || 0; recompute(); flash(`최소 참여 농가 ${S.farmMin}명`); });
  $("inShock").addEventListener("input", (ev) => { S.shock = Number(ev.target.value); renderPayout(); });
  bindSeg("segArea", "areaMin", Number, "면적 기준");
  bindSeg("segPay", "pay", String, "지급 방식");
  [1, 2, 3].forEach((i) => $(`tab${i}`).addEventListener("click", () => selectTab(i)));
  $("btnReset").addEventListener("click", () => { S.off.clear(); S.sel = null; recompute(); flash("처음 상태로"); });
  $("refused").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-restore]"); if (!b) return;
    const f = b.dataset.restore;
    F.farms.get(f).parcels.forEach((id) => S.off.delete(id));
    S.sel = null; recompute(); flash(`${f} 복구`);
    ($("refused").querySelector("[data-restore]") || $("btnReset")).focus();
  });
  $("farmPickList").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-farm]"); if (!b) return;
    const f = b.dataset.farm; toggleFarm(f);
    $("farmPickList").querySelector(`[data-farm="${CSS.escape(f)}"]`)?.focus();
  });
  ["mouseover", "focusin"].forEach((t) => $("farmPickList").addEventListener(t, (ev) => hlFarm(ev.target.closest("[data-farm]")?.dataset.farm)));
  $("farmPickList").addEventListener("mouseleave", () => hlFarm(null));
  $("decide").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-pick]"); if (!b) return;
    S.sel = b.dataset.pick; selectTab(1); recompute();
    $("map").scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  });
  $("btnSheet").addEventListener("click", () => { renderSheet(); $("sheetDlg").showModal(); });
  $("btnClose").addEventListener("click", () => $("sheetDlg").close());
  $("btnPrint").addEventListener("click", () => window.print());
  $("btnCopy").addEventListener("click", async () => {
    let ok = true;
    try { await navigator.clipboard.writeText(location.href); } catch { ok = false; }
    const b = $("btnCopy");
    $("copyNote").textContent = ok ? "지금 화면의 링크를 복사했습니다" : "복사하지 못했습니다. 주소창의 주소를 복사해 보내세요";
    b.textContent = ok ? "복사했습니다 ✓" : "복사 실패";
    clearTimeout(b.resetT); b.resetT = setTimeout(() => { b.textContent = "링크 복사"; }, 2500);
  });
  // 건너뛰기 링크가 주소의 #를 바꾸면 화면 상태로 잘못 읽으므로, 이동만 하고 주소는 그대로 둔다
  document.querySelector(".skip").addEventListener("click", (ev) => { ev.preventDefault(); $("results").focus(); });
  addEventListener("hashchange", () => { if (location.hash && !location.hash.includes("=")) return; readHash(); recompute(); });
  $("segSheet").addEventListener("click", (ev) => {
    const b = ev.target.closest("button"); if (!b) return;
    $("segSheet").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    S.sheet = b.dataset.v; renderSheet();
  });
  readHash();
  recompute();
}
document.addEventListener("DOMContentLoaded", init);
