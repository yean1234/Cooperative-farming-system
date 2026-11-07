/* 모두의 농장 공통: 글자 크기(보통·크게·더 크게)
 * <head>에서 바로 불러 첫 화면부터 고른 크기로 그린다. 고른 값은 이 브라우저에만 저장한다. */
"use strict";
(function () {
  const KEY = "mf-font-scale";
  let v = "0";
  try { v = localStorage.getItem(KEY) || "0"; } catch (e) { /* 저장소를 못 쓰면 보통으로 */ }
  if (!["0", "1", "2"].includes(v)) v = "0";
  document.documentElement.dataset.fs = v;
  document.addEventListener("DOMContentLoaded", () => {
    // 본문 바로가기: 주소의 #을 바꾸지 않고 이동만 한다 (화면 상태를 # 뒤에 담는 페이지가 있다)
    document.querySelector(".skip-main")?.addEventListener("click", (ev) => {
      const main = document.getElementById("main"); if (!main) return;
      ev.preventDefault(); main.focus(); main.scrollIntoView();
    });
    const btns = document.querySelectorAll("[data-fs-set]");
    const mark = () => btns.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.fsSet === v)));
    mark();
    btns.forEach((b) => b.addEventListener("click", () => {
      v = b.dataset.fsSet;
      document.documentElement.dataset.fs = v;
      try { localStorage.setItem(KEY, v); } catch (e) { /* 저장 못 해도 지금 화면에는 적용 */ }
      mark();
      // 지도(Leaflet 등)가 바뀐 크기에 맞춰 다시 그리도록 알린다
      window.dispatchEvent(new Event("resize"));
    }));
  });
})();

/* 계산 서버가 필요한 화면(후보단지 찾기 · 단지 운영 설계)이 쓴다.
 * GitHub Pages처럼 파일만 올린 곳에는 서버가 없으므로, 오류 대신 무엇을 하면 되는지 알려 준다. */
window.MF = {
  async serverUp() {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      return r.ok && (await r.json()).status === "ok";
    } catch (e) { return false; }
  },
  showServerNotice(box) {
    box.classList.add("info");
    box.innerHTML = "<b>이 화면은 계산 서버가 있어야 동작합니다.</b> 지금 주소는 파일만 올린 공개 데모라 서버가 없습니다. "
      + "서버 없이 바로 쓸 수 있는 <a href=\"empty-field.html\">거절 대응(빈 필드 시뮬레이터)</a>을 써 보세요. "
      + "이 화면은 내 컴퓨터에서 <code>python -m server</code>로 서버를 켜고 열면 됩니다.";
    box.hidden = false;
  },
};
