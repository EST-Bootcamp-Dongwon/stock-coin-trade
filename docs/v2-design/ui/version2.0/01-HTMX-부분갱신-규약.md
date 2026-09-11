# HTMX 부분 갱신 규약

> **문서 버전** v2.0 · **작성일** 2026-08-11 (KST)
> **스택** HTMX 2 + Alpine.js 3 + Tailwind CSS
> **목적** "어디까지를 한 덩어리로 갱신할 것인가"를 화면마다 즉흥적으로 정하지 않기 위한 규약

---

## 1. 왜 규약이 필요한가

> **Django 관점** — Next.js/React 에서는 컴포넌트가 상태를 갖고 스스로 리렌더된다.
> HTMX 는 다르다. **서버가 HTML 조각을 돌려주고 브라우저가 특정 DOM 을 갈아끼운다.**
> 따라서 "무엇을 갈아끼울지"를 **서버와 템플릿이 미리 합의**해야 한다.
> 이 합의를 매번 즉흥적으로 하면 화면마다 규칙이 달라져 유지보수가 안 된다.

---

## 2. 3계층 구조

모든 화면을 **페이지 → 프래그먼트 → 셀** 3계층으로 나눈다.

```
페이지 (Page)           전체 문서. 첫 로드에만 렌더
 └── 프래그먼트 (Fragment)   HTMX 로 통째 교체되는 단위. 독립 URL 을 가짐
      └── 셀 (Cell)         값 하나. OOB 스왑으로 갱신
```

| 계층 | 예 | 템플릿 위치 | 갱신 방식 |
|---|---|---|---|
| 페이지 | 대회 대시보드 | `contests/dashboard.html` | 전체 로드 |
| 프래그먼트 | 보유 잔고 표 | `contests/_holdings.html` | `hx-get` → `outerHTML` |
| 셀 | 현재가 하나 | `_price_cell.html` | `hx-swap-oob="true"` |

### 2.1 프래그먼트 명명 규칙

- 템플릿 파일명은 **언더스코어로 시작** (`_holdings.html`) — 직접 렌더되지 않는 조각임을 표시
- URL 은 `/fragments/` 하위에 둔다 → `/contests/<id>/fragments/holdings/`
- URL name 은 `contests:frag_holdings`

---

## 3. 프래그먼트 목록 (화면별)

### 3.1 대회 대시보드 (`/contests/<id>/me/`)

| 프래그먼트 | URL | 갱신 트리거 | 주기 |
|---|---|---|---|
| `_market_bar` | `.../fragments/market-bar/` | 폴링 | 30초 |
| `_intraday_chart` | `.../fragments/intraday-chart/` | 폴링 | 60초 |
| `_nav_chart` | `.../fragments/nav-chart/` | 로드 시 1회 | — |
| `_guideline_table` | `.../fragments/guidelines/` | 폴링 + 주문 후 | 60초 |
| `_sector_chart` | `.../fragments/sectors/` | 주문 후 | 이벤트 |
| `_pending_orders` | `.../fragments/pending-orders/` | 폴링 + 주문 후 | 10초 |
| `_order_targets` | `.../fragments/order-targets/` | 폴링 + 주문 후 | 15초 |
| `_holdings` | `.../fragments/holdings/` | 폴링 + 주문 후 | 15초 |

```html
<!-- 예: 보유 잔고 -->
<div id="holdings"
     hx-get="{% url 'contests:frag_holdings' contest.id %}"
     hx-trigger="every 15s, order:filled from:body"
     hx-swap="outerHTML">
  {% include "contests/_holdings.html" %}
</div>
```

### 3.2 연습 거래 화면 (`/practice/stock/`)

| 프래그먼트 | 트리거 | 주기 |
|---|---|---|
| `_quote_panel` (현재가·등락) | 폴링 | 10초 |
| `_orderbook` (호가창) | 폴링 | 10초 |
| `_positions` (보유) | 폴링 + 주문 후 | 15초 |
| `_movers` (상승·하락 TOP) | 폴링 | 30초 |
| `_watchlist` (관심종목) | 이벤트 | — |
| `_order_form` (주문 폼) | 종목 변경 시 | 이벤트 |

### 3.3 랭킹 (`/contests/<id>/ranking/`)

| 프래그먼트 | 트리거 | 주기 |
|---|---|---|
| `_ranking_table` | 폴링 | 60초 |
| `_participant_modal` | 클릭 | 이벤트 |

**랭킹은 60초면 충분하다.** [F-05](../../features/version2.0/F-05-랭킹-정산-스냅샷.md) 7장에서
정한 대로 장중 순위는 10분 스냅샷 기준이라 그보다 자주 당길 이유가 없다.

---

## 4. 이벤트 규약 ★

주문 하나가 여러 프래그먼트를 바꾼다. **서버가 이벤트를 쏘고 프래그먼트들이 듣는다.**

### 4.1 서버 → 클라이언트 이벤트

Django 뷰가 응답 헤더로 이벤트를 발행한다.

```python
response["HX-Trigger"] = json.dumps({
    "order:filled": {"symbol": "005930", "side": "BUY"},
    "toast": {"level": "success", "message": "체결되었습니다."},
})
```

| 이벤트 | 발생 시점 | 듣는 프래그먼트 |
|---|---|---|
| `order:filled` | 주문 체결 | 보유 잔고 · 주문 타겟 · 가이드라인 · 섹터 |
| `order:rejected` | 주문 거부 | 미접수/에러 주문 · 토스트 |
| `order:cancelled` | 주문 취소 | 미접수/에러 주문 · 주문 타겟 |
| `account:reset` | 계좌 초기화 | 보유 · 이력 · 요약 |
| `watchlist:changed` | 관심종목 변경 | 관심종목 · 시장 탭 |
| `toast` | 공통 알림 | 토스트 컨테이너 |

### 4.2 왜 이벤트인가

주문 폼이 "체결됐으니 보유 테이블도 갱신해줘"를 **직접 알 필요가 없다.**
프래그먼트를 추가해도 주문 폼을 고치지 않는다. 결합도가 낮아진다.

---

## 5. 폴링 정책

### 5.1 폴링 주기 기준

| 데이터 | 주기 | 근거 |
|---|---|---|
| 미체결 주문 | 10초 | 체결 여부를 빨리 알아야 한다 |
| 시세·호가 | 10초 | 캐시 TTL(10초)과 맞춘다 |
| 보유·주문타겟 | 15초 | |
| 가이드라인·지수 | 30~60초 | 자주 바뀌지 않는다 |
| 랭킹 | 60초 | 스냅샷이 10분 단위 |

**캐시 TTL 보다 짧게 폴링하지 않는다.** 어차피 같은 값이 온다.

### 5.2 폴링 중단 조건 ★

```html
hx-trigger="every 10s [document.visibilityState === 'visible']"
```

| 조건 | 처리 |
|---|---|
| **탭이 백그라운드** | 폴링 중단 |
| **장 마감 후** | 서버가 응답에 `HX-Trigger: {"market:closed": {}}` 를 실어 보내고, 클라이언트가 폴링 주기를 늘림 |
| 대회 종료 | 폴링 완전 중단 |

**서버리스 함수 호출 = 비용**이다. 보지 않는 화면을 갱신하지 않는다.

---

## 6. Alpine.js 의 역할 분담

| 담당 | 기술 |
|---|---|
| 서버 데이터가 필요한 갱신 | **HTMX** |
| 순수 클라이언트 상태 (모달 열림·탭 전환·슬라이더·즉시 계산) | **Alpine.js** |

### 예: 물타기 모달

| 동작 | 담당 |
|---|---|
| 모달 열기/닫기 | Alpine (`x-show`) |
| 슬라이더 움직임 → 평단 재계산 | **Alpine** (즉시 반응 필요, 서버 왕복 없음) |
| 한도 여유 조회 | HTMX (모달 열 때 1회) |
| 주문 실행 | HTMX (`hx-post`) |

---

## 7. 로딩·에러 표시

### 7.1 로딩

```html
<div hx-get="..." hx-indicator="#spinner-holdings">
<div id="spinner-holdings" class="htmx-indicator">...</div>
```

- **폴링 갱신에는 스피너를 띄우지 않는다.** 15초마다 깜빡이면 산만하다
- 사용자 액션(주문·검색)에만 표시

### 7.2 에러

| 상황 | 처리 |
|---|---|
| 프래그먼트 갱신 실패 | **직전 내용을 유지**하고 작은 경고 배지만 표시. 화면을 비우지 않는다 |
| 주문 실패 | 폼 자리에 [F-04](../../features/version2.0/F-04-대회-규칙엔진.md) 4.2 의 에러 구조를 렌더 |
| 인증 만료 | `HX-Redirect` 헤더로 로그인 페이지 이동 |

> **v1.0 의 교훈** — KRX 뉴스가 실패하면 조용히 빈 배열이 왔다.
> **실패를 화면에 드러낸다.**

---

## 8. CSRF

```html
<body hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'>
```

`<body>` 에 한 번 걸면 모든 HTMX 요청에 상속된다.

---

## 9. 관련 문서

- 화면 목록 → [00-화면목록-라우팅.md](00-화면목록-라우팅.md)
- 계좌 스냅샷 응답 → [F-09](../../features/version2.0/F-09-보유자산-포트폴리오.md) 3장
