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
