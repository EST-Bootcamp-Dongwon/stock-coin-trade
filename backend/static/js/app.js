/* ─────────────────────────────────────────────────────────────────
   공통 클라이언트 스크립트.

   ★★ **여기 있는 것이 이 프로젝트 자바스크립트의 거의 전부다.**

   v1.0 은 `common.js` 686줄이 GNB · AI 패널 · RAG · 뉴스를 전부 손으로 그렸다.
   v2.0 은 그 일을 서버(Django 템플릿) + HTMX + Alpine 이 나눠 맡는다.
   남는 것은 **둘 중 누구의 일도 아닌 것** 뿐이다 — 토스트 표시와 폴링 절약.

   Django 관점 ─────────────────────────────────────────────────────────
   Next.js 였다면 `<Toaster />` 컴포넌트와 상태 훅이 있었을 자리다. HTMX 에는
   컴포넌트가 없고 **이벤트**가 있다. 서버가 응답 헤더로 이벤트를 쏘고
   (`HX-Trigger`), 여기서 그것을 듣는다.
   ───────────────────────────────────────────────────────────────── */

(function () {
  "use strict";

  var TOAST_MS = 4000;

  var TOAST_STYLE = {
    success: "bg-slate-900 text-white",
    info: "bg-slate-900 text-white",
    warn: "bg-warn text-white",
    error: "bg-danger text-white",
  };

  /** 토스트 한 장을 띄운다. 일정 시간 뒤 스스로 사라진다. */
  function showToast(level, message) {
    var container = document.getElementById("toast-container");
    if (!container || !message) return;

    var el = document.createElement("div");
    el.className =
      "toast pointer-events-auto w-full max-w-md rounded-lg px-4 py-3 text-sm shadow-lg " +
      (TOAST_STYLE[level] || TOAST_STYLE.info);
    // ★ innerHTML 이 아니라 textContent 다. 서버 메시지에 종목명·별칭 같은
    //   사용자 입력이 섞여 들어오므로, HTML 로 해석하면 XSS 통로가 된다.
    el.textContent = message;
    container.appendChild(el);

    setTimeout(function () {
      el.style.transition = "opacity 300ms";
      el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 300);
    }, TOAST_MS);
  }

  /* ── ① 서버가 보낸 토스트 이벤트 (규약 4.1) ──────────────────
     `HX-Trigger: {"toast": {"level": "success", "message": "체결되었습니다."}}`
     HTMX 가 이 헤더를 보고 `toast` 라는 이름의 CustomEvent 를 body 에 발생시킨다.
     우리는 그것을 듣기만 하면 된다. */
  document.body.addEventListener("toast", function (event) {
    var detail = event.detail || {};
    showToast(detail.level, detail.message);
  });

  /* ── ② 페이지 로드 시 뜬 토스트도 자동으로 사라지게 한다 ─────
     Django 메시지로 서버가 미리 그려 넣은 것들이다 (_toast.html). */
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("#toast-container .toast").forEach(function (el) {
      setTimeout(function () {
        el.style.transition = "opacity 300ms";
        el.style.opacity = "0";
        setTimeout(function () { el.remove(); }, 300);
      }, TOAST_MS);
    });
  });

  /* ── ③ 프래그먼트 갱신 실패를 화면에 드러낸다 (규약 7.2) ──────
     ★★ v1.0 의 교훈 — KRX 뉴스 수집이 실패하면 조용히 빈 배열이 왔고,
        화면은 아무 말 없이 비었다. 사용자는 "뉴스가 없다" 고 믿었다.

     HTMX 는 4xx·5xx 응답의 본문을 화면에 넣지 않는다(기본 동작). 그래서
     **직전 내용이 그대로 남는다** — 규약이 요구한 "화면을 비우지 않는다" 는
     이미 만족한다. 여기서는 거기에 **작은 경고 배지**를 덧붙인다. */
  document.body.addEventListener("htmx:responseError", function (event) {
    var target = event.detail && event.detail.target;
    if (!target || target.querySelector(".frag-error")) return;

    var badge = document.createElement("div");
    badge.className =
      "frag-error mt-1 rounded bg-warn-soft px-2 py-1 text-[11px] text-amber-800 ring-1 ring-amber-300";
    badge.textContent =
      "갱신 실패 (" + (event.detail.xhr ? event.detail.xhr.status : "?") + ") — 아래는 마지막으로 받은 값입니다.";
    target.appendChild(badge);
  });

  /* 갱신이 다시 성공하면 경고 배지를 걷는다. */
  document.body.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail && event.detail.target;
    if (target) {
      target.querySelectorAll(".frag-error").forEach(function (el) { el.remove(); });
    }
  });

  /* ── ④ 인증 만료 → 로그인 화면 (규약 7.2) ─────────────────────
     폴링 도중 세션이 끊기면 403 이 돌아온다. 그대로 두면 화면은 멀쩡해 보이는데
     아무것도 갱신되지 않는 상태가 이어진다. */
  document.body.addEventListener("htmx:responseError", function (event) {
    var xhr = event.detail && event.detail.xhr;
    if (xhr && xhr.status === 403 && xhr.getResponseHeader("HX-Redirect")) {
      window.location.href = xhr.getResponseHeader("HX-Redirect");
    }
  });
})();
