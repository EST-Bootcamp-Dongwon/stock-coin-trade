# `quant-core` 이식 경계표 — 무엇을 어디로 미는가

> ⚠️ **2026-09-15 갱신** — `quant-core`·`backtest-service` 는 개발하지 않기로 해 제거됐다.
> **이식 계획 자체는 폐기됐다** — 옮겨 갈 저장소가 없다. 다만 §3 의 체결 가정 명세
> (11호가 이후 가정 체결)와 §1~§2 의 순수 함수/부수효과 경계 분석은 **이 저장소
> 체결 엔진의 설계·회귀 문서**로서 계속 유효하다. 아래 단계표·제안 시그니처는
> "이식할 때의 절차" 기록으로 그대로 두되, 지금 실행할 계획은 아니다.

> **이 세션에서 코드를 옮기지 않는다.** 이 문서는 옮길 때 무엇을 어디로 밀지 정한 **표**다.
> 근거: [ADR-SC-0002](../decisions/0002-체결엔진-원본과-이식.md)
> 대상: `backend/trading/services.py` (1,132줄) · 기준 커밋 `9b90f49`

---

## 1. 이미 순수한 것 / 아직 아닌 것

`FillResult`·`Fill` 이 ORM 이 아닌 `dataclass` 라, 절반은 이미 옮길 수 있는 상태다.

| 함수 | 순수한가 | 이식 |
|---|---|---|
| `calc_cost(gross, side, fee_bp, tax_bp)` | ✅ 완전 | 그대로 |
| `resolve_limit_price(...)` | ✅ 완전 | 그대로 |
| `match_orderbook(...)` | ✅ 완전 | 그대로 |
| `match_at_price(price, qty)` | ✅ 완전 | 그대로 |
| `_price_ok` · `_fill_assumed_depth` · `_round_price` · `_round_qty` | ✅ 완전 | 그대로 |
| `tick_size` · `price_limits` (`market/quotes.py`) | ✅ 완전 | 그대로 |
| `Orderbook` · `Level` · `Quote` | ✅ frozen dataclass | 그대로 |
| `cost_rates(account)` | ❌ ORM + 대회 규칙 | **가지 않는다** → [ADR-SC-0004](../decisions/0004-세율-테이블은-quant-core-소관.md) |
| `apply_fills(order, result)` | ❌ ORM 쓰기 6곳 | **경계 이동 대상** ← 이 문서의 본론 |
| `_apply_buy` / `_apply_sell` | ❌ `.save()` · `.delete()` | **경계 이동 대상** |
| `place_order` · `fill_open_order` · `cancel_order` | ❌ 트랜잭션·락·규칙엔진 | 여기 남는다 (adapter) |

✅ 표시된 것은 **[골든 테스트 23건](../../backend/trading/golden_test.py)이 값을 고정해 뒀다.**
이식 후 `quant-core` 에서 같은 입력을 넣어 `__snapshots__/golden_test.ambr` 와 대조하면 된다.

---

## 2. 경계표 — `apply_fills` 의 부수효과를 어디로 미는가

`apply_fills` 안에서 **DB 를 건드리는 지점은 여섯 곳**이다. 전부 호출부로 민다.

| # | 위치 | 지금 하는 일 | 밀어낼 곳 | 순수 함수는 대신 무엇을 하나 |
|---|---|---|---|---|
| 1 | `apply_fills:451` | `cost_rates(account)` — `Contest`·`AppSetting` 조회 | **호출부(adapter)** | `fee_bp`·`tax_bp` 를 **인자로 받는다** |
| 2 | `apply_fills:456` | `_lock_position()` — `select_for_update()` | **호출부** | `PositionState` 를 **인자로 받는다** |
| 3 | `_apply_buy:514` | `account.cash -= spend` · `account.save()` | **호출부** | 새 `AccountState` 를 **반환한다** |
| 4 | `_apply_buy:535` | `position.qty/avg_price/principal` · `position.save()` | **호출부** | 새 `PositionState` 를 **반환한다** |
| 5 | `_apply_sell:573` | `position.delete()` (잔량 0) | **호출부** | `PositionState(qty=0, ...)` 를 반환. **삭제 판단은 adapter 가** |
| 6 | `apply_fills:490` · `_record_executions` | `order.save()` · `Execution` 행 생성 | **호출부** | `OrderDelta` 와 `fills` 를 반환 |

### 읽기만 하는 것 — 인자로 바꾸면 되는 값

| 지금 | 무엇 | 인자로 |
|---|---|---|
| `order.side` | BUY / SELL | `side: str` |
| `order.symbol` · `order.asset_class` | 반올림 단위 결정 | `asset_class: str` |
| `order.filled_qty` · `gross_amount` · `fee` · `tax` · `realized_pnl` | 누적 집계 | `OrderState` |
| `order.requested_qty` | FILLED / PARTIAL 판정 | `OrderState` |
| `account.cash` | 잔고 검사 | `AccountState` |
| `position.qty` · `avg_price` · `principal` | 평단·원금 계산 | `PositionState` |

### 순수 함수가 **하지 않을** 판단

경계를 옮길 때 같이 옮기고 싶어지지만, **남겨야 하는** 것들이다.

- 🔒 **락** — `select_for_update()` 순서(계좌 → 포지션)는 교착을 피하는 규약이다. adapter 가 지킨다.
- 🔒 **트랜잭션 경계** — `transaction.atomic()`. 순수 함수는 트랜잭션을 모른다.
- 🔒 **행 삭제** — 잔량 0 일 때 `Position` 행을 지우는 것은 저장 정책이지 계산이 아니다.
- 🔒 **대회 규칙 검증** — `contest_rules` 는 `place_order` 단계이고 체결 계산 밖이다.
- ⚠️ **`OrderRejected` 예외 2종**(`INSUFFICIENT_CASH`·`INSUFFICIENT_POSITION`)은
  **순수 함수가 그대로 던진다.** 계산 불변식이지 저장 문제가 아니다.
  다만 `quant-core` 는 Django 를 모르므로 예외 클래스도 같이 이식한다.

---

## 3. 11호가 이후 **가정 체결** 규칙 — 명세 ★

> DoD 항목. **문서화되지 않으면 이식본이 원본과 같은지 검증할 수 없다.**
> 출처: `_fill_assumed_depth()` (`services.py:345-412`) · F-03 5.1 4단계

호가 10단계를 다 먹고도 수량이 남으면, 11호가부터를 **가정**해 체결을 잇는다.

### 알고리즘

```
전제: levels = book.opposite(side)   — 가격 0 인 단계는 이미 걸러졌고, 유리한 순으로 정렬돼 있다

0. levels 가 비어 있으면 → 가정 체결을 하지 않는다        ← 근거가 없으면 지어내지 않는다
1. avg_qty = _round_qty( sum(모든 단계의 잔량) / len(levels), asset_class )
     · ★ 분모는 **실제 호가 단계 수 전체**다. 이미 소진한 단계도 포함한다
     · ★ 자산군 단위로 절사한다 — 주식 43.33주 같은 조각을 만들지 않는다 (E-38)
     · avg_qty <= 0 이면 중단
2. price = levels[-1][0]                                  ← 가장 불리한 **실제** 호가에서 출발
3. upper, lower = price_limits(quote.prev_close)           ← quote 가 없으면 (0, 0) = 벽 없음
4. 최대 MAX_ASSUMED_LEVELS(=50) 회 반복:
     a. tick = tick_size(price)                            ← ★ 매 회 다시 계산한다
     b. price = price + tick  (매수)  /  price - tick  (매도)
     c. level_no += 1
     d. 중단 조건 — 하나라도 걸리면 break:
          · price <= 0
          · 매수이고 upper > 0 이고 price > upper           ← 상한가가 벽
          · 매도이고 lower > 0 이고 price < lower           ← 하한가가 벽
          · 지정가 조건 위반 (_price_ok)
     e. take = min(remaining, avg_qty)
     f. Fill(qty=take, price=price, price_level=level_no, is_assumed_depth=True)
5. 그래도 남으면 remaining 으로 반환 → PARTIAL, 시장 체결 추종 대상이 된다
```

### 이식할 때 틀리기 쉬운 곳 — 여섯 가지

| # | 함정 | 왜 |
|---|---|---|
| 1 | 평균 잔량의 **분모** | 남은 단계가 아니라 **전체 단계** 수다. 헷갈리면 값이 조용히 달라진다 |
| 2 | `_round_qty` 를 빠뜨림 | 주식이 43.33주 체결된다. **실측에서 실제로 나왔던 버그**다 (E-38) |
| 3 | `tick_size` 를 루프 밖에서 한 번만 계산 | 가격대를 넘어가면 호가단위가 바뀐다. 밖에서 계산하면 어긋난다 |
| 4 | 출발 가격을 `levels[0]` 으로 | `[-1]` 이다 — **가장 불리한** 실제 호가에서 이어야 한다 |
| 5 | 상·하한가 벽을 빠뜨림 | 시장가(STOP 발동)는 지정가로 멈추지 않아 **이것이 유일한 제동**이다 |
| 6 | `is_assumed_depth` 플래그를 안 세움 | 화면 배지와 감사 근거가 사라진다 (E-03 5.1) |

### ⚠️ 이 가정은 실제 시장보다 **유리하다**

진짜 호가창은 멀어질수록 잔량이 얇아진다. 평균 잔량이 무한히 이어진다고 보면
**실제보다 좋은 가격에 더 많이 체결된다.** 타임폴리오 규칙이 그렇게 정의돼 있어 따르되,
그 사실을 숨기지 않는다 — 조각마다 `is_assumed_depth=True` 가 붙어 화면에 배지로 뜬다.

> 🔍 **백테스트로 넘어갈 때 다시 볼 것.** 모의투자 대회에서는 "규칙이 그렇다" 로 충분하지만,
> 백테스트 성과 지표에 쓰면 **체결 비용이 체계적으로 과소평가된다.**
> `backtest-service` 는 이 가정을 끄거나 잔량 감쇠 모델로 바꿀 선택지를 가져야 한다.

---

## 4. 제안 시그니처 — `quant-core.matching.fills`

```python
@dataclass(frozen=True)
class AccountState:
    cash: int                       # 원 단위 정수

@dataclass(frozen=True)
class PositionState:
    qty: Decimal
    avg_price: Decimal
    principal: int

@dataclass(frozen=True)
class OrderState:
    side: str
    asset_class: str
    requested_qty: Decimal
    filled_qty: Decimal = Decimal(0)
    gross_amount: int = 0
    fee: int = 0
    tax: int = 0
    realized_pnl: int = 0

@dataclass(frozen=True)
class ApplyResult:
    account: AccountState
    position: PositionState          # qty == 0 이면 adapter 가 행을 지운다
    order: OrderState
    fills: tuple[Fill, ...]
    cost: dict[str, int]             # {"fee": …, "tax": …, "realized_pnl": …}
    status: str                      # FILLED / PARTIAL

def apply_fills_pure(
    account: AccountState,
    position: PositionState | None,
    order: OrderState,
    result: FillResult,
    *,
    fee_bp: Decimal,
    tax_bp: Decimal,
) -> ApplyResult: ...
```

**`frozen=True` 가 핵심이다.** dict 를 쓰면 오타 난 키가 조용히 무시되지만,
frozen dataclass 는 입력을 실수로 변형하는 순간 즉시 터진다. 리팩터링 중에는 이게 낫다.

### adapter 가 이렇게 얇아진다

```python
# backend/trading/services.py — 이식 후
def apply_fills(order: Order, result: FillResult, *, at=None) -> Order:
    if not result.fills:
        return order
    at = at or timezone.now()
    account = order.account
    fee_bp, tax_bp = cost_rates(account)              # ① 대회 규칙은 여기 남는다
    position = _lock_position(account, order.symbol)  # ② 락도 여기 남는다

    r = apply_fills_pure(
        to_account_state(account), to_position_state(position), to_order_state(order),
        result, fee_bp=fee_bp, tax_bp=tax_bp,
    )

    Account.objects.filter(pk=account.pk).update(cash=r.account.cash, updated_at=at)   # ③
    if r.position.qty <= 0:
        position.delete()                                                              # ⑤
    else:
        _upsert_position(account, order, r.position)                                   # ④
    _save_order(order, r, at)                                                          # ⑥
    _record_executions(order, result, at)
    return order
```

---

## 5. 이식 순서 — 이 순서를 바꾸지 않는다

| # | 할 일 | 완료 판정 |
|---|---|---|
| 0 | ✅ **골든 테스트로 현재 값 고정** | `pytest` 23건 통과 (완료 · 2026-08-17) |
| 1 | `apply_fills` 를 **계산과 저장으로 쪼갠다** — 아직 같은 파일 안에서 | `manage.py test trading` 그대로 통과 |
| 2 | 계산부에 대한 골든 테스트 추가 (`apply_fills_pure` 스냅샷) | 새 스냅샷 생성 |
| 3 | `quant-core` 로 **복사**하고 이 저장소는 아직 자기 것을 쓴다 | 양쪽 골든 값이 **같다** |
| 4 | 이 저장소를 `pip install quant-core` 로 바꾸고 adapter 만 남긴다 | 311건 + 23건 전부 통과 |
| 5 | 원본 계산 코드를 지운다 | `git diff` 로 삭제분 설명 가능 |

> ⚠️ **3번에서 바로 4번으로 건너뛰지 않는다.** 복사본이 같은 답을 낸다는 것을
> 확인하기 전에 원본을 지우면, 틀렸을 때 되돌아갈 기준이 사라진다.
