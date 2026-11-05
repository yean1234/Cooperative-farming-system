const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType,
  BorderStyle, AlignmentType, PageOrientation, ImageRun, LevelFormat, HeadingLevel, PageBreak, VerticalAlign,
} = require('docx');

const FONT = 'Noto Sans KR';
const NAVY = '11325A', GREEN = '2F8F5B', GRAY = 'F4F6F9', GREENBG = 'E7F4EC', YELLOW = 'FFF6CC', RED = 'FBEAE7', LINE = 'E5E9F0', MUTED = '6B7280', GOLD = 'FFD54A';
const PAGE_W = 16838, PAGE_H = 11906, MARGIN = 720;
const CW = PAGE_W - 2 * MARGIN; // 15398

// ---------- helpers ----------
function runs(text, base = {}) {
  // **bold** markup only
  const out = [];
  const parts = String(text).split('**');
  parts.forEach((t, i) => {
    if (!t) return;
    out.push(new TextRun({ text: t, font: FONT, size: base.size || 20, color: base.color, bold: i % 2 === 1 || !!base.bold }));
  });
  return out;
}
function p(text, o = {}) {
  return new Paragraph({
    children: runs(text, o),
    spacing: { before: o.before ?? 0, after: o.after ?? 80, line: o.line ?? 276 },
    alignment: o.align,
    shading: o.fill ? { type: ShadingType.CLEAR, color: 'auto', fill: o.fill } : undefined,
    keepNext: o.keepNext,
  });
}
function kicker(t) { return p(t, { size: 17, color: GREEN, bold: true, after: 40 }); }
function title(t) {
  return new Paragraph({ heading: HeadingLevel.HEADING_1, children: runs(t, { size: 36, color: NAVY, bold: true }) });
}
function sub(t) { return p(t, { size: 20, color: '4B5563', after: 160 }); }
function bullets(items, size = 19) {
  return items.map(t => new Paragraph({
    children: runs(t, { size }), numbering: { reference: 'bul', level: 0 }, spacing: { after: 40, line: 264 },
  }));
}
function numbered(items, size = 19) {
  return items.map(t => new Paragraph({
    children: runs(t, { size }), numbering: { reference: 'num', level: 0 }, spacing: { after: 60, line: 264 },
  }));
}
function spacer(after = 120) { return new Paragraph({ children: [], spacing: { after } }); }
const noBorder = { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' };
const NOB = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder, insideHorizontal: noBorder, insideVertical: noBorder };
const thin = { style: BorderStyle.SINGLE, size: 4, color: LINE };
const THIN = { top: thin, bottom: thin, left: thin, right: thin, insideHorizontal: thin, insideVertical: thin };

function cell(children, width, o = {}) {
  if (!Array.isArray(children)) children = [children];
  children = children.map(c => (typeof c === 'string' ? p(c, { size: o.size || 18, color: o.color, bold: o.bold, after: 40 }) : c));
  return new TableCell({
    children, width: { size: width, type: WidthType.DXA },
    shading: o.fill ? { type: ShadingType.CLEAR, color: 'auto', fill: o.fill } : undefined,
    margins: { top: o.pad ?? 80, bottom: o.pad ?? 80, left: 120, right: 120 },
    verticalAlign: o.valign || VerticalAlign.TOP,
  });
}
// data table: header row navy, body rows; rowFill: array of fill per row or null; cellFill: map "r,c" -> fill
function dtable(headers, rows, widths, o = {}) {
  const trs = [];
  if (headers) trs.push(new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, widths[i], { fill: o.headFill || NAVY, color: 'FFFFFF', bold: true, size: o.hsize || 17 })) }));
  rows.forEach((r, ri) => {
    trs.push(new TableRow({
      children: r.map((c, ci) => {
        const fill = (o.cellFill && o.cellFill[`${ri},${ci}`]) || (o.rowFill && o.rowFill[ri]) || undefined;
        const bold = ci === 0 && o.firstBold !== false;
        const color = (o.cellColor && o.cellColor[`${ri},${ci}`]) || (bold ? NAVY : undefined);
        return cell(c, widths[ci], { fill, bold, color, size: o.size || 18 });
      }),
    }));
  });
  return new Table({ columnWidths: widths, width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA }, borders: THIN, rows: trs });
}
// card: shaded single-cell table
function card(heading, body, fill = GRAY, width = CW, o = {}) {
  const kids = [];
  if (heading) kids.push(p(heading, { size: 20, bold: true, color: o.headColor || NAVY, after: 40 }));
  (Array.isArray(body) ? body : [body]).forEach(b => kids.push(typeof b === 'string' ? p(b, { size: 18, color: o.color, after: 40, line: 264 }) : b));
  return new Table({ columnWidths: [width], width: { size: width, type: WidthType.DXA }, borders: NOB, rows: [new TableRow({ children: [cell(kids, width, { fill, pad: 120 })] })] });
}
// columns: array of arrays of block elements; widths DXA
function cols(colsArr, widths) {
  return new Table({
    columnWidths: widths, width: { size: widths.reduce((a, b) => a + b, 0), type: WidthType.DXA }, borders: NOB,
    rows: [new TableRow({ children: colsArr.map((kids, i) => new TableCell({ children: kids, width: { size: widths[i], type: WidthType.DXA }, margins: { top: 0, bottom: 0, left: i === 0 ? 0 : 140, right: i === colsArr.length - 1 ? 0 : 140 } })) })],
  });
}
function img(path, wpx, hpx) {
  return new Paragraph({ children: [new ImageRun({ type: 'png', data: fs.readFileSync(path), transformation: { width: wpx, height: hpx } })], spacing: { after: 60 } });
}
function caption(t) { return p(t, { size: 15, color: MUTED, after: 100 }); }
function steps(items, hitIdx = []) {
  // horizontal process row
  const n = items.length; const w = Math.floor(CW / n);
  const widths = Array(n).fill(w); widths[n - 1] = CW - w * (n - 1);
  return new Table({
    columnWidths: widths, width: { size: CW, type: WidthType.DXA }, borders: NOB,
    rows: [new TableRow({ children: items.map((it, i) => {
      const hit = hitIdx.includes(i);
      return cell([
        p(it[0], { size: 18, bold: true, color: hit ? GOLD : NAVY, align: AlignmentType.CENTER, after: 20 }),
        p(it[1], { size: 16, color: hit ? 'FFFFFF' : undefined, align: AlignmentType.CENTER, after: 0, line: 252 }),
      ], widths[i], { fill: hit ? NAVY : GRAY, pad: 100 });
    }) })],
  });
}
function pb() { return new Paragraph({ children: [new PageBreak()] }); }
function foot(n) { return p(`팀 물개 · 빈 필드 시뮬레이터 · OPEN HARBOR 2026 1차 제출 · ${n}`, { size: 14, color: '8A94A6', before: 120, after: 0, align: AlignmentType.RIGHT }); }

const half = Math.floor(CW / 2);
const L = (f) => Math.floor(CW * f);

// ---------- content ----------
const body = [];

// 1. 표지
body.push(
  p('OPEN HARBOR 2026 · 비즈니스톤 · 1차 피드백 자료 · 2026. 11. 5', { size: 17, color: GREEN, bold: true, after: 300 }),
  p('팀 물개  ·  포스텍 · 한동대 학생팀', { size: 22, color: '4B5563', after: 80 }),
  new Paragraph({ heading: HeadingLevel.TITLE, children: runs('빈 필드 시뮬레이터', { size: 60, bold: true, color: NAVY }) }),
  p('공동영농 법인을 준비하는 **경북 평야의 대표·사무장**이 농가 한 곳의 거절로 생긴 **빈 필지** 때문에 지원 기준(20ha)과 평당 배당을 다시 계산하지 못하는 문제를, 공개 필지 데이터로 **대안 단지와 배당 변화를 1초 안에 보여 주는 웹 시뮬레이터**로 해결합니다.', { size: 24, line: 330, after: 200 }),
  p('현재 단계: MVP 개발 중   |   형태: 웹(PC·태블릿) + 인쇄 설명 자료   |   시제품: 포항 흥해 실측 2,666필지', { size: 18, color: GREEN, bold: true, after: 200 }),
  img('img/empty-field-top.png', 700, 360),
  caption('시제품 화면 — 흥해 들녘 단지 6(23.2ha)에서 농가 3곳이 거절한 상황. 기준 미충족 판정과 1순위 대안(이웃 1 편입)이 맨 위에 뜹니다.'),
  foot(1), pb(),
);

// 2. 문제
body.push(
  kicker('02 문제'), title('농가 한 곳이 거절하면, 사무장은 2주를 감으로 보냅니다'),
  sub('문제 문단은 가운데(우리가 만난 자리)부터 썼습니다. 큰 현상은 통계를 붙이고, 아이템은 그 뒤에 둡니다.'),
  cols([
    [
      card('큰 현상', '농가 인구 중 65세 이상이 **51.0%**(국가데이터처, 2025 농림어업총조사 확정, 2026.9), 경북 농가는 **59.2%**(동북지방통계청, 2024)입니다. 정부는 땅 주인은 그대로 두고 법인이 함께 짓는 공동영농을 **2030년 100개소**까지 늘리고, 20ha·농업인 5명 이상 법인에 **2년 최대 20억 원**을 줍니다(농식품부, 2026.3). 면적 기준을 50ha로 올리고 연접도를 보는 개선안이 **2026년 11월** 나옵니다(농식품부, 2026.8~9).', GRAY, half),
      spacer(100),
      card('우리가 만난 자리 · 2026.10.29 경주시청·상주시청 농정과 전화 인터뷰', '"구역을 정하고 신청서까지, 법인은 **몇 개월**이 걸린다." "이미 선정된 법인은 도면이 지도에 다 만들어져 있고, 거절 단지가 생겨도 인근지로 넓히면 된다는 걸 안다. 필요한 쪽은 **2028년 신청을 준비하는 예비 법인**이다." 문경·영덕 법인 대표는 농가를 설득하려고 설명회를 **10회 이상** 열었다고 말했습니다(농민신문, 2025.1).', 'DCE6F2', half),
      spacer(100),
      card('문제 한 문장', '공동영농 법인을 준비하는 사무장은 농가 한 곳이 거절하면 ① 20ha를 지킬 수 있는지 ② 남은 조합원의 평당 배당이 얼마나 주는지 ③ 대신 어느 땅의 주인을 찾아가야 하는지를 **숫자 없이** 정하고, 설명회를 다시 엽니다.', GREENBG, half),
    ],
    [
      dtable(['문제의 크기', '숫자', '기준·출처'], [
        ['설득 비용', '설명회 10회 이상, 1~1.5년', '문경·영덕 법인 대표 발언(농민신문 2025.1)'],
        ['1년 늦게 묶으면', '20ha 단지 기준 약 **1억 4천만 원** 소득을 놓침', '문경 늘봄 평당 2,350원→4,700원(경북매일 2024.9)으로 계산'],
        ['지원 자격', '2년 최대 20억 원이 1년 뒤로 밀림', '공모는 1년 단위(농식품부 2026.3)'],
        ['배당 설명', '농가별 73만~1,800만 원, **24.7배** 격차', '영덕 달산 2024 첫 배당(농민신문 2025.1)'],
        ['배당 근거', '지구별 평당 1,500~4,000원, 산정 근거 공개된 적 없음', '경북 배당 법인 10개소(경북매일 2026.3)'],
        ['수치 만들 사람', '영농 디지털 기술을 안 쓰는 법인 **58.6%**', '2024년 조사(원문 재확인 중)'],
      ], [1900, 2900, 2899], { size: 17 }),
      spacer(100),
      card('왜 지금인가', '정부 민·관 협의체가 "농지 집적화"와 "소득 배분 기준"을 남은 과제로 꼽고 11월 개선안을 냅니다(2026.7.27). 경북 공동영농 지구는 2023년 2곳에서 2026년 29~31곳으로 늘었습니다(경북도). 예비 법인이 가장 많이 생기는 해가 2027년입니다.', YELLOW, half),
    ],
  ], [half, CW - half]),
  foot(2), pb(),
);

// 3. 솔루션
body.push(
  kicker('03 솔루션'), title('지도에서 거절한 농가를 누르면, 1초 안에 "빼고 갈지·메울지"가 나옵니다'),
  sub('웹 시뮬레이터 두 화면과 인쇄 자료 한 장. 머신러닝이 아니라 공개 필지 데이터 위의 제약 최적화입니다.'),
  cols([
    [
      img('img/empty-field-top.png', 470, 242),
      caption('빈 필드 시뮬레이터 — 흥해 단지 6(23.2ha)에서 3농가 5.2ha가 빠지자 **18.1ha로 기준 미충족**. 법인 순수익 −22.7%, 남은 조합원 평당 배당은 −0.5%. 1순위 대안 "이웃 1 편입(+2.4ha)". 판정 0.01초.'),
      img('img/sites-map.png', 150, 161),
      p('**후보단지 시뮬레이터(예비 법인용 앞 단계)** — 읍면 지도에서 면적·농가 수·도로 접속·경사·배수 조건을 만족하는 필지 묶음을 제안합니다. 흥해 2,666필지·489ha에서 후보 9곳·253ha를 **0.25초**에 찾습니다.', { size: 17, color: '4B5563' }),
    ],
    [
      card('① 기준 판정 + 대체 필지 추천', '거절 필지를 빼면 면적(20ha 현행 / 50ha 상향 검토)과 최소 농가 수 충족 여부를 즉시 판정합니다. 못 채우면 기준과 법인 목표 순수익을 함께 맞추는 이웃 필지 편입 후보를 순위로 보여 줍니다.', GRAY, CW - half),
      spacer(80),
      card('② 평당 배당과 흉년 시나리오', '지급 방식(배당형·고정 임차료·혼합)별로 남은 조합원의 평당 배당을 범위로 보여 주고, 흉년이면 손해를 누가 지는지(배당형은 조합원, 임차료형은 법인) 첫 줄에 적습니다. 시뮬레이션 결과, 한 농가가 빠져 법인 순수익이 6% 줄어도 평당 배당은 약 1%만 줍니다. 땅과 배당받을 사람이 함께 빠지기 때문입니다.', GRAY, CW - half),
      spacer(80),
      card('③ 설명 자료 만들기(인쇄)', '대표 보고용 한 장과 농가 설명용 큰 글자 한 장을 뽑습니다. 농어민 디지털 역량은 일반 국민의 72.4%, 고령층은 56.2%라(NIA 2025 디지털정보격차 실태조사) 조합원은 종이로 받습니다.', GRAY, CW - half),
      spacer(80),
      card('어떻게 계산하나', '팜맵·흙토람·연속지적도(공개 데이터, 인증키 2개)로 필지 인접 그래프를 만들고, 연결성·면적·농가 수·도로·경사 제약 아래 묶음을 탐색합니다. 서버 없이 브라우저에서 계산합니다. 소유자는 비공개라 필지 수로 농가 수를 추정합니다.', GREENBG, CW - half),
    ],
  ], [half, CW - half]),
  foot(3), pb(),
);

// 4. 고객
body.push(
  kicker('04 고객 · 인터뷰에서 배운 것'), title('인터뷰 2건이 고객을 바꿨습니다: 선정된 법인 → 설립을 준비하는 예비 법인'),
  sub('2026.10.29 경주시청·상주시청 농정과 공동영농 담당자 전화 인터뷰. 법인 대표 소개는 "바빠서 어렵다"며 거절당했습니다.'),
  cols([
    [
      card(null, ['**"선정된 법인은 도면이 지도에 다 만들어져 있다. 거절 단지가 생겨도 인근지로 확대하면 된다는 걸 안다."**', p('→ 선정 법인에게 우리 도구는 \'이미 아는 것\'. 지역 사정은 법인이 더 잘 압니다.', { size: 16, color: MUTED, after: 0 })], GRAY, half),
      spacer(60),
      card(null, ['**"지금 필요한 건 사업을 신청하려는 쪽이다. 예를 들어 2028년을 준비하는 법인."**', p('→ 예비 법인은 구역·농가 수·배당 수치를 처음 만듭니다. 신청서에 면적·연접·참여 농가·배분 계획을 써야 합니다.', { size: 16, color: MUTED, after: 0 })], GRAY, half),
      spacer(60),
      card(null, ['**"한창 작업 중이라 법인이 워낙 바빠 외부 연락은 끊고 있다. 연락처를 드리긴 힘들다."**', p('→ 인터뷰 자체가 이 시장의 병목입니다. 시군 담당자 소개 없이는 닿기 어렵습니다.', { size: 16, color: MUTED, after: 0 })], GRAY, half),
      spacer(100),
      dtable(['', '인터뷰 전', '인터뷰 후'], [
        ['1순위 고객', '선정 법인의 사무장·대표', '**설립·신청을 준비하는 예비 법인 대표·사무장**'],
        ['쓰는 때', '거절 당일', '**구역 구상 → 설득(거절) → 신청서 작성**'],
        ['채널', '법인 콜드콜', '**시군 예비법인 교육·설명회, 농어촌공사 지사**'],
      ], [1500, 2600, 3599], { size: 17, cellFill: { '0,2': GREENBG, '1,2': GREENBG, '2,2': GREENBG } }),
    ],
    [
      p('**예비 법인의 1년: 어디서 우리 도구를 쓰나**', { size: 19, color: NAVY, after: 60 }),
      dtable(null, [[
        '**교육 참석**\n강진군 2027 공모 대비 리더 교육 40여 명(2026.10)'.replace('\n', ' — '),
        '**구역 구상** — 후보단지 시뮬레이터로 핵심 필지 1~2개 찾기',
        '**설명회·거절** — 빈 필드 판정, 1순위 대안, 농가용 한 장',
        '**신청서** — 면적·연접·농가 수·배분 계획 수치 표',
        '**시군 추천·선정** — 2년 20억',
      ]], [1540, 1540, 1540, 1540, 1539], { size: 15, firstBold: false, cellFill: { '0,1': NAVY, '0,2': NAVY, '0,3': NAVY }, cellColor: { '0,1': 'FFFFFF', '0,2': 'FFFFFF', '0,3': 'FFFFFF' } }),
      spacer(100),
      card('예비 법인을 밖에서 알아보는 신호', bullets([
        '시군·도의 공동영농 리더 교육, 예비사업자 교육 참석(2027 공모 대비 교육은 2026.10 시작)',
        '확산사업 공모·추가공모 신청 이력(경북도 2026 추가공모 공지)',
        '확장 계획 공표(경주 대청 65ha → 2030년 220ha, 경북매일 2025.12)',
      ], 17), GRAY, CW - half),
      spacer(100),
      card('아직 모르는 것', '예비 법인 본인 인터뷰는 0건입니다. "거절 뒤 재설계에 2주"는 아직 가설이고, 다음 2주 검증 대상입니다(8장).', RED, CW - half),
    ],
  ], [half, CW - half]),
  foot(4), pb(),
);

// 5. 시장
body.push(
  kicker('05 시장'), title('1년 차에 닿을 고객은 경북의 예비 법인 10곳입니다'),
  sub('조 단위 시장 대신, 누가 이 문제를 겪고 그중 우리가 연락할 수 있는 곳이 몇 곳인지로 내려왔습니다.'),
  cols([
    [
      dtable(['층', '정의', '규모', '근거'], [
        ['정책 목표', '공동영농법인 육성', '2030년 100개소 · 경북 100개소·9천ha', '농식품부 2026.3 · 경북도'],
        ['조직화 들녘', '들녘경영체·공동경영체·공동영농 지구', '**500~700곳, 6만~10만ha**', '2015년 말 224개소·51,114ha 기준 추정'],
        ['2027~28 도달 가능', '확산사업 누적 36 + 경북 31지구 + 예비사업자', '**연 100~200곳**', '2027 예산안, 경북도 공고'],
        ['1년 차 목표', '경북 시군 예비법인 교육 참석 법인', '**10곳**', '경주·상주·구미·문경·의성'],
      ], [1700, 2300, 2000, 1699], { size: 16, rowFill: [null, null, null, GREENBG] }),
      spacer(100),
      cols([
        [card(null, [p('**2곳 → 29곳**', { size: 34, color: NAVY, after: 20 }), p('경북 공동영농 지구, 2023 → 2026(경북도)', { size: 15, color: '4B5563', after: 0 })], GRAY, Math.floor(half / 2) - 70)],
        [card(null, [p('**6 → 36**', { size: 34, color: NAVY, after: 20 }), p('공동영농 확산 선정 법인 누적, 2026 → 2027(예산안)', { size: 15, color: '4B5563', after: 0 })], GRAY, half - Math.floor(half / 2) - 70)],
      ], [Math.floor(half / 2), half - Math.floor(half / 2)]),
      spacer(100),
      card('1년 차 매출 감각', '10곳 × 설계 1회 2,000만 원 = **2억 원**(보조금 교육·컨설팅 비목을 거칠 때) / 구독만이면 10곳 × 연 100만 원 = 1,000만 원. 비목 인정 여부가 갈림길입니다(7장).', YELLOW, half),
    ],
    [
      dtable(['', '한국', '일본', '핀란드·덴마크'], [
        ['필지 구조', '필지당 0.13~0.18ha, 농가당 5~8필지(대리값)', '집적률 62.1%, 2030년 KPI 70%', '교환 설계를 공공이 수행'],
        ['돈 내는 주체', '보조금 비목·시군(농가 직접 지불 약함)', '국가 정액 보조, 시정촌 위탁', 'ha당 €1,000~1,300 / 3,500DKK 보조'],
        ['설계 도구', '없음. 종이 지도와 감', '농업위원회 지원 시스템(정보 관리까지)', '측량 기관 전문가'],
        ['기준값', '집적률·분산도 공표 임계값 없음', '1ha 団地 비율이 보조금 요건', '거리 단축 법정 요건'],
      ], [1300, 2300, 2100, 1999], { size: 16 }),
      spacer(100),
      card('국내와 해외의 차이가 주는 뜻', '한국은 "얼마나 모였는가"를 재는 공식 지표가 없습니다. 흥해 실측처럼 공개 데이터로 읍면 지표를 먼저 계산해 공개하는 쪽이 기준값을 쓰게 됩니다. 11월 개선안에 집적화 지표가 들어가면 설계 수요가 제도가 됩니다.', GRAY, CW - half),
      spacer(100),
      p('주의: 500~700곳·연 100~200곳은 공개 수치로 만든 추정치입니다. "5명·20ha 이상 벼 중심 법인" 수는 통계에 없습니다. 해외 숫자는 2차 자료가 섞여 있어 원문 확인 전입니다.', { size: 15, color: MUTED }),
    ],
  ], [half, CW - half]),
  foot(5), pb(),
);

// 6. 경쟁
body.push(
  kicker('06 경쟁 · 차별성'), title('같은 도구는 없지만 경쟁자는 있습니다. 법인 대표의 머릿속 지도입니다'),
  sub('인터뷰에서 확인한 가장 흔한 대안은 "그냥 안다"입니다. 우리가 지는 줄도 그대로 두었습니다.'),
  dtable(['비교 항목', '대표의 현장 지식 + 종이 지도', '시군·농어촌공사 컨설팅', '팜맵·농업e지(공개 지도)', '측량·경지정리 용역', '빈 필드 시뮬레이터'], [
    ['거절 뒤 대안까지', '수일~2주(답사)', '수주(일정 조율)', '해당 없음(조회만)', '수개월', '**판정 1초, 당일 결정**'],
    ['20/50ha·5명 기준 판정', '암산', '사람이 확인', '없음', '있음(유료)', '**있음, 기준 바꿔 재계산**'],
    ['평당 배당·흉년 시나리오', '감으로 약속', '일부', '없음', '없음', '**방식별 범위 + 흉년**'],
    ['농가에게 건넬 자료', '말', '보고서', '없음', '도면', '**큰 글자 한 장 인쇄**'],
    ['지역 사정(이장·친분·소유자)', '가장 잘 앎', '잘 앎', '없음', '없음', '없음. 소유자 비공개라 필지 수로 추정'],
    ['신뢰', '수십 년 관계', '공공기관', '공공', '면허', '학생팀, 실적 0'],
    ['비용', '0원', '개소당 3,000만, 보조 90%', '무료', '수천만 원', '구독 연 100만/50ha 또는 설계비'],
  ], [2600, 2700, 2500, 2300, 2200, 3098], { size: 17, cellFill: {
    '0,5': GREENBG, '1,5': GREENBG, '2,5': GREENBG, '3,5': GREENBG,
    '4,1': YELLOW, '4,2': YELLOW, '4,5': YELLOW, '5,1': YELLOW, '5,2': YELLOW, '5,5': YELLOW,
  } }),
  spacer(140),
  cols([
    [card('우리만 하는 것 ①', '거절 당일에 숫자가 나옵니다. 기준·순수익·평당 배당·1순위 대안을 한 줄로.', GREENBG, L(0.25) - 100)],
    [card('우리만 하는 것 ②', '공개 데이터만으로 읍면 전체(2,666필지)를 1초 안에 돌립니다. 법인 자료가 없어도 시작합니다.', GREENBG, L(0.25) - 100)],
    [card('우리만 하는 것 ③', '결과가 그대로 신청서 수치 표와 농가 설명 자료가 됩니다. 한 번 계산으로 서류 셋.', GREENBG, L(0.25) - 100)],
    [card('지는 곳을 메우는 방법', '지역 사정은 법인이 입력하고 우리는 계산합니다. 현장 동행(Get)으로 시작해 셀프 도구(Keep)로 넘깁니다.', RED, CW - 3 * L(0.25) - 100)],
  ], [L(0.25), L(0.25), L(0.25), CW - 3 * L(0.25)]),
  foot(6), pb(),
);

// 7. 수익
body.push(
  kicker('07 수익 모델'), title('돈은 농가가 아니라 보조금 비목과 시군이 냅니다'),
  sub('컨설팅 자부담이 30%에서 50%로 오르자 신청이 1,448건(2009)에서 203건(2016)으로 줄었습니다. 농가 직접 과금은 보조 수단으로만 둡니다.'),
  cols([
    [
      dtable(['누가', '왜 내나', '얼마', '가격 앵커'], [
        ['① 법인이 쥔 보조금의 교육·컨설팅 비목', '신청서·총회 자료가 설계 보고서로 나옴. 자부담 10%', '**설계 1회 2,000~3,000만 원**', '들녘경영체 교육·컨설팅 개소당 3,000만 원(전남 2024). 공동영농 확산 지원 범위에 교육·컨설팅 포함'],
        ['② 시군 농정과', '예비 법인 발굴용 읍면 후보지 지도', '읍면 3~10곳 **5,000만 원~**', '농촌공간계획 용역 4~4.6억 원(110여 시군)'],
        ['③ 법인 구독(사후 운영)', '거절·변경 때마다 다시 계산', 'ha당 연 2만 원 **(50ha 연 100만 원)**', '미국 농업 SW 이용자 51%가 에이커당 연 $5 미만(Purdue 2019)'],
      ], [2000, 2000, 1800, 2199], { size: 16 }),
      spacer(100),
      card('확인해야 넘어가는 문턱', '공동영농 확산 시행지침 원문에서 교육·컨설팅 비목에 **설계 용역·소프트웨어**가 들어가는지 아직 확인하지 못했습니다. 안 되면 인증 컨설팅 업체와 하도급으로 시작합니다. 농지법 제7조의2(임대차 중개 금지) 때문에 서비스는 \'설계·자문\'이고 매물·수수료 기능은 넣지 않습니다.', YELLOW, half),
    ],
    [
      p('**가격은 원가와 대체재 사이 어디쯤인가 (50ha 단지 기준)**', { size: 19, color: NAVY, after: 80 }),
      card('천장 · 지금 대안에 드는 돈', '컨설팅 개소당 3,000만 원 / 설명회 10회·1~1.5년의 설득 비용', 'D9DEE7', CW - half),
      spacer(60),
      card('지켜 주는 돈 · 거절 1농가(1.8ha)를 메우면', '규모화 절감 연 약 320만 원 되찾음. 10a당 직접생산비 70세 이상 68.6만 원 → 5ha 이상 50.8만 원(국가데이터처 2025년산 논벼 생산비조사, 2026.3)', GREENBG, CW - half),
      spacer(60),
      card('우리 가격 · 구독 연 100만 원', '되찾는 320만 원의 약 1/3. 설계 1회 2,000~3,000만 원은 보조금 비목에서', YELLOW, CW - half),
      spacer(60),
      card('바닥 · 원가', '공공데이터 키 2개 무료, 서버 월 수만 원(브라우저 계산), 들녘별 현장 보정 인건비가 거의 전부', 'DCE6F2', CW - half),
      spacer(60),
      p('2030년 100개소가 모두 50ha라면 구독 연 1억 원, 비목 설계가 연 10억 원 규모(가정). 금액은 인터뷰로 검증합니다.', { size: 15, color: MUTED }),
    ],
  ], [half, CW - half]),
  foot(7), pb(),
);

// 8. 성과
body.push(
  kicker('08 성과 · 검증 계획'), title('사용자 0명, 매출 0원. 대신 실측 시제품과 인터뷰 2건, 다음 2주 계획이 있습니다'),
  sub('통과 기준은 결과를 보기 전에 적었습니다. 칭찬은 세지 않고 연락처·재방문·일정만 셉니다.'),
  cols([
    [
      card('만든 것', '포항 흥해 실측 2,666필지·489ha 지도(팜맵·흙토람·연속지적도). 후보단지 9곳 0.25초, 빈 필드 판정 0.01초, 대표·농가용 설명 자료 인쇄. 소스는 깃허브 저장소에 있습니다.', GRAY, L(0.42)),
      spacer(70),
      card('만난 사람', '2026.10.29 경주시청·상주시청 농정과 공동영농 담당 2명(전화). 법인 대표 0명(소개 거절), 농어촌공사 0명.', GRAY, L(0.42)),
      spacer(70),
      card('발표', '2026.11.1 포스텍-한동대 Mini TeX-Corps 최종발표. 비즈니스 모델 캔버스와 경험만 묻는 인터뷰 질문지 작성.', GRAY, L(0.42)),
      spacer(70),
      card('없는 것', '실제 사용자, 매출, 계약·MOU, 실제 단지 경계로 한 정확도 검증(가상 읍면 F1만 있음, 성능 근거로 쓰지 않음).', RED, L(0.42)),
    ],
    [
      dtable(['다음 2주 (11.5~11.19)', '목표', '통과 기준', '마감'], [
        ['예비 법인·교육 참석 농가 인터뷰', '5명', '3명 이상이 "거절 뒤 재설계에 1주 이상 걸렸다"고 말함', '11.12'],
        ['시군 예비법인 교육·설명회 일정 확보', '경북 2곳', '참관 또는 10분 소개를 허락한 곳 1곳', '11.19'],
        ['농어촌공사 경주·상주지사 통화', '1건', '컨설팅 때 쓰는 기준표 유무와 임차료 수준 확인', '11.12'],
        ['시제품 현장 시연', '2곳', '화면을 본 뒤 연락처를 남기거나 다음 약속을 잡은 곳 1곳', '11.19'],
        ['수익 가설 확인', '2명', '"지난해 필지 정리·컨설팅에 얼마 썼고 누가 결재했나"에 금액이 나옴', '11.19'],
      ], [2900, 1100, 4000, 930], { size: 16 }),
      spacer(100),
      card('인터뷰에서 묻는 것(경험만)', '"마지막 거절 뒤 무엇부터 하셨나요?" "대체 땅을 정하기까지 며칠, 몇 번 답사하셨나요?" "남은 농가가 무엇을 물었나요?" "거절 때문에 지원사업에서 탈락한 적이 있나요?" "쓰시겠어요?"는 묻지 않습니다.', GREENBG, CW - L(0.42)),
    ],
  ], [L(0.42), CW - L(0.42)]),
  foot(8), pb(),
);

// 9. 글로벌
body.push(
  kicker('09 글로벌 진출 전략'), title('첫 해외는 일본입니다. 같은 소필지·고령화 구조에 집적 KPI가 제도로 있습니다'),
  sub('직접 판매가 아니라 시정촌·JA·수탁 기관에 \'설계 계산 레이어\'를 공급하는 방식입니다. 현지 인터뷰 0건, 가설 단계입니다.'),
  cols([
    [
      card('왜 일본인가', bullets([
        '농지 집적률 62.1%, 2030년 목표 70%. 1ha 이상 団地 비율과 団地당 면적이 보조금 요건으로 정해져 있어 결과를 숫자로 팔 수 있음',
        '地域計画(지역계획)을 전 시정촌이 세워야 하고 국가가 정액 보조. 민간 위탁 사례 있음(山梨県 341만 엔)',
        '농업위원회 지원 시스템은 농지 정보 관리까지만. 배치·교환 설계 계산은 비어 있음',
        '평균 분산지수 0.80 같은 실측 기준값이 공개돼 있어 우리 지표를 바로 비교할 수 있음',
      ], 17), GRAY, half),
      spacer(100),
      card('현지 제약', '소유·경작자 데이터는 농업위원회가 쥐고 있고 개인정보 규제가 강함. 集落(마을) 단위 합의 관행, 일본 농지법, 일본어 UI와 현지 영업 인력. 위 수치는 2차 자료가 섞여 있어 원문 확인 전입니다.', YELLOW, half),
    ],
    [
      dtable(null, [[
        '**2027** — 국내 경북 예비 법인 3곳 설계 레퍼런스, 설계 전후 KPI(블록 수·1ha 블록 비율·거점 거리) 공개',
        '**2028** — 일본어 UI, eMAFF 農地ナビ 공개 필지 데이터로 시정촌 1곳 시범 계산',
        '**2029** — JA 또는 지역계획 수탁사 1곳과 화이트라벨·API 계약',
      ]], [2566, 2566, 2567], { size: 16, firstBold: false, cellFill: { '0,2': NAVY }, cellColor: { '0,2': 'FFFFFF' } }),
      spacer(100),
      dtable(['국가', '진출 이유', '방식', '순서'], [
        ['일본', '같은 구조, KPI 제도화, 설계 계산 공백', '수탁사·JA 파트너, 레이어 공급', '1순위'],
        ['핀란드·덴마크', '교환 설계에 ha당 보조(€1,000~1,300 / 3,500DKK)', '공공 측량 기관 알고리즘 공급', '2순위'],
        ['동남아', '소필지 구조는 같으나 지적·공개 데이터 부족', '보류', '—'],
      ], [1500, 3000, 2300, 899], { size: 16 }),
      spacer(100),
      card('국내에서 먼저 증명할 것', '해외에 가져갈 것은 화면이 아니라 "설계 전후 블록 수가 얼마나 줄었는가"라는 숫자입니다. 경북 실측 3곳의 전후 KPI가 그 증거입니다.', GREENBG, CW - half),
    ],
  ], [half, CW - half]),
  foot(9), pb(),
);

// 10. 팀
const q = Math.floor(CW / 4);
body.push(
  kicker('10 팀'), title('2026년 8월에 이 문제를 만나, 10월에 실측 필지로 시제품을 만든 팀 물개'),
  sub('경력 대신 "이 문제를 언제 만났고 지금 무엇을 하는지"를 적었습니다. 비어 있는 역할도 그대로 적었습니다.'),
  steps([
    ['2026.8', '주제 재정의: 흩어진 논밭을 어떻게 묶어야 공동영농이 되는가'],
    ['2026.9~10', '흥해 실측 2,666필지 지도, 후보단지·빈 필드 시뮬레이터'],
    ['2026.10.29', '경주·상주 시청 농정과 인터뷰, 타겟 전환'],
    ['2026.11.1', '포스텍-한동대 Mini TeX-Corps 최종발표'],
    ['2026.11.5', 'OPEN HARBOR 1차 제출'],
  ], [4]),
  spacer(140),
  cols([
    [card('팀원 1 · 이름·학과 (기입)', ['**역할(예시):** 기획·고객 인터뷰', '**지금 하는 일:** 시군·법인 인터뷰 섭외, 질문지'], YELLOW, q - 100)],
    [card('팀원 2 · 이름·학과 (기입)', ['**역할(예시):** 데이터·알고리즘', '**지금 하는 일:** 팜맵·흙토람 연동, 제약 최적화'], YELLOW, q - 100)],
    [card('팀원 3 · 이름·학과 (기입)', ['**역할(예시):** 웹 화면', '**지금 하는 일:** 빈 필드 화면, 설명 자료 인쇄'], YELLOW, q - 100)],
    [card('팀원 4 · 이름·학과 (기입)', ['**역할(예시):** 사업모델·발표', '**지금 하는 일:** 수익 근거, BMC, 공모 자료'], YELLOW, CW - 3 * q - 100)],
  ], [q, q, q, CW - 3 * q]),
  spacer(140),
  cols([
    [card('비어 있는 역할: 농업 현장 전문가', '공동영농 법인을 만들어 본 사람이 팀에 없습니다. 채울 방법: 시군 담당자 소개로 예비 법인 대표 1명 자문, 대구경북연구원 공동영농 교육 강사 인터뷰. 기한 11월 말.', RED, half - 100)],
    [card('이 팀이 할 수 있는 이유', '공개 데이터만으로 실제 읍면(흥해 2,666필지)을 돌리는 시제품을 두 달 안에 만들었고, 인터뷰 결과에 따라 고객을 바꿨습니다. 포스텍·한동대의 데이터·컴퓨팅 수업과 창업 과정(Mini TeX-Corps)을 함께 거쳤습니다.', GRAY, CW - half - 100)],
  ], [half, CW - half]),
  spacer(100),
  p('※ 노란 칸은 제출 전 팀원 이름·학과·실제 역할로 바꿔 주세요. 팀원 사진을 넣으면 좋습니다.', { size: 15, color: MUTED }),
  foot(10), pb(),
);

// 11. 요청
body.push(
  kicker('11 요청 사항'), title('돈보다 먼저, 인터뷰할 사람 다섯 명을 소개해 주세요'),
  sub('이 시장은 인터뷰가 가장 어렵고 가장 필요합니다. 법인은 바쁘고, 학생팀의 전화는 끊깁니다. 소개 한 번이 가장 큰 투자입니다.'),
  cols([
    [
      dtable(['요청', '구체적으로', '우리가 들고 갈 것'], [
        ['① 인터뷰 연결', '공동영농을 준비 중인 예비 법인 대표·사무장 3명, 시군 농정과(포항·구미·문경 중) 1명, 농어촌공사 경북본부 1명', '흥해 실측 시제품 시연, 10분 질문지(경험만 묻는 질문), 결과 공유'],
        ['② 채널 접근', '경북도·시군의 공동영농 리더 교육, 예비사업자 교육, 설명회에 참관 또는 10분 소개 기회', '농가용 설명 자료 한 장 샘플'],
        ['③ 멘토링', '보조금 교육·컨설팅 비목으로 설계·소프트웨어 비용을 받는 구조(시행지침 해석), 농지법 중개 금지와 \'설계·자문\'의 선', '수익 흐름 근거 문서'],
        ['④ 공모전·학술대회 추천', '기술(공간 데이터·제약 최적화) 또는 지역(경북 농업대전환) 성격에 맞는 공모전, 학술대회 추천', '알고리즘·실험 설계 문서'],
        ['⑤ 돈을 쓴다면', '투자 요청은 없습니다. 소액(300만 원)이면 흙토람 전수 조회, 현장 교통비, 시연용 태블릿', '지출 내역 공개'],
      ], [2100, 4300, 2839], { size: 16, rowFill: [GREENBG] }),
    ],
    [
      card('다음 목표 시점', ['**11.12 데모데이**까지 인터뷰 5건과 시연 2곳.', '**2027.3 공모(2027년 선정)** 전에 경북 예비 법인 3곳 무료 설계, 설계 전후 KPI 공개.', '**2027 하반기** 보조금 비목 또는 시군 용역으로 첫 유료 계약 1건.'], 'DCE6F2', CW - L(0.6)),
      spacer(100),
      card('가장 확신이 없는 것', '예비 법인이 우리 도구를 \'지금\' 필요로 하는지, 그리고 예비 법인을 어디서 만나는지입니다. 시군 담당자 두 분의 말로 방향을 바꿨지만, 예비 법인 본인의 말은 아직 한 마디도 듣지 못했습니다.', GRAY, CW - L(0.6)),
    ],
  ], [L(0.6), CW - L(0.6)]),
  foot(11), pb(),
);

// 12. 부록
body.push(
  kicker('부록'), title('공통 질문지 답변과 숫자 출처'),
  cols([
    [
      card('준비 과정에서 고민한 점', numbered([
        '시군 담당자 인터뷰 뒤 고객을 \'선정 법인\'에서 \'설립·신청을 준비하는 예비 법인\'으로 바꿨습니다. 예비 법인은 아직 조직과 예산이 없어 찾기도, 돈을 받기도 어렵습니다. 이 전환이 맞는지, 예비 법인을 어디서 어떻게 만나야 하는지 고민입니다.',
        '법인이 바빠 시군 담당자가 소개를 거절했습니다. 학생팀이 공동영농 법인 대표를 만나려면 어떤 경로가 현실적인지 고민입니다(교육·세미나 채널을 생각하고 있습니다).',
        '농가·법인이 직접 돈을 내기 어려워 보조금 교육·컨설팅 비목과 시군 용역(B2G)을 수익원으로 잡았습니다. 학생팀이 B2G로 시작할 수 있는지, 구독과 설계 용역 중 무엇을 앞세울지 고민입니다.',
      ], 17), GRAY, L(0.55)),
      spacer(80),
      card('가장 피드백 받고 싶은 부분', '"예비 공동영농 법인"을 1순위 고객으로 잡은 것이 타당한지, 그리고 시군·도의 예비법인 교육·설명회를 첫 채널로 쓰는 방법이 현실적인지입니다.', GREENBG, L(0.55)),
      spacer(80),
      card('현재 단계', 'MVP 개발 중 — 흥해 실측 필지로 만든 시제품은 있으나 실제 사용자는 없습니다.', GRAY, L(0.55)),
    ],
    [
      p('**숫자 출처**', { size: 18, color: NAVY, after: 40 }),
      ...bullets([
        '국가데이터처, 2025 농림어업총조사 확정 결과(2026.9) · 2025년산 논벼 생산비조사(2026.3.27)',
        '동북지방통계청, 최근 10년 경북 농업 변화(2025.12)',
        '농식품부, 공동영농 확산 지원 시범사업 6개소 선정(2026.3) · 민관 협의체 출범(2026.7.27) · 차관 현장 점검, 면적 기준 상향 검토(2026.8.28) · 장관 구미 샘물영농조합 방문(2026.9.3)',
        '경북도·경북매일, 공동영농 지구 21→29~31곳, 배당 법인 10개소(2026.3.17), 대청 확장 계획(2025.12.10), 문경 늘봄 배당(2024.9.6)',
        '농민신문, 영덕 달산 첫 배당·설명회 10여 차례(2025.1.13)',
        '과기정통부·NIA, 2025 디지털정보격차 실태조사(2026.3)',
        '메트로신문, 전남 식량작물공동경영체 교육·컨설팅 개소당 3천만 원(2024)',
        'Purdue University, Farmer use of ag data software(2019)',
        '외교저널, 강진군 2027 공모 대비 공동영농 리더 교육(2026.10)',
        'KREI 2016 들녘경영체 조사, 2015년 말 224개소·51,114ha · 농업법인 조사(2025)',
        '경주시청·상주시청 농정과 공동영농 담당 전화 인터뷰(2026.10.29, 팀 기록)',
        '일본·유럽 수치: 농림수산성 집적률, 地域計画 제도, 핀란드·덴마크 교환 보조 — 2차 자료, 원문 확인 전',
      ], 14),
      p('계산값(1억 4천만 원, 320만 원, 500~700곳 등)은 위 공개 수치로 팀이 산출한 추정치입니다. 가상 읍면 정확도(F1)는 성능 근거로 쓰지 않았습니다.', { size: 14, color: MUTED, before: 60 }),
    ],
  ], [L(0.55), CW - L(0.55)]),
  foot(12),
);

const doc = new Document({
  creator: '팀 물개',
  title: '비즈니스톤_물개_빈필드시뮬레이터',
  styles: {
    default: { document: { run: { font: FONT, size: 20 } } },
    paragraphStyles: [
      { id: 'Title', name: 'Title', basedOn: 'Normal', next: 'Normal', run: { font: FONT, size: 60, bold: true, color: NAVY }, paragraph: { spacing: { before: 0, after: 200 }, keepNext: true } },
      { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { font: FONT, size: 36, bold: true, color: NAVY }, paragraph: { spacing: { before: 0, after: 80 }, keepNext: true, outlineLevel: 0 } },
    ],
  },
  numbering: {
    config: [
      { reference: 'bul', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 360, hanging: 240 } } } }] },
      { reference: 'num', levels: [{ level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 400, hanging: 300 } } } }] },
    ],
  },
  sections: [{
    properties: { page: { size: { width: PAGE_H, height: PAGE_W, orientation: PageOrientation.LANDSCAPE }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
    children: body,
  }],
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(process.argv[2] || 'deck.docx', buf); console.log('written', buf.length); });
