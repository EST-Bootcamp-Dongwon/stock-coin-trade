"""질문 → 의도. **거절은 규칙이 책임지고, 문자 n-gram 은 네 의도만 가른다.**

## 🔴 왜 모델이 아닌가 (2026-09-14 실측 · ADR-SC-0013 ①)

한국어 질문 32개로 재 봤다 — 문자 n-gram 21/32 · 한국어 SBERT 22/32 · model2vec 18/32 ·
다국어 MiniLM 17/32. **규칙을 앞에 두면 n-gram 이 27/32, 거절 누락 0** 이었다.
모델이 얻는 것은 동의어 몇 개였고, 대가는 의존성 · 메모리(최대 +1.7GB) · CPU 간 비결정성이었다.

🔴 **임베딩 유사도로는 "왜 반도체 안 사?" 를 거절하지 못했다** — 도메인 낱말을 공유해
범위 안 질문에 붙는다(유사도 0.873). 위험한 질문일수록 가깝다. 그래서 거절은 **유사도가
아니라 규칙**이 한다. 모델을 들이려면 `agent_test` 의 질문 평가셋에서 이겨야 한다.

## 🔒 순서가 곧 설계다

    ① normalize → ② 거절 규칙을 **가리기 전 문장에** → ③ 섹터 이름을 찾아 가린다(lexicon)
    → ④ 거절 규칙을 가린 문장에 한 번 더 → ⑤ 두 섹터 이상이면 거절 → ⑥ 섹터 이름이 애매하면 되묻기
    → ⑦ "몇 위가 어디냐" 는 순위 표를 가리킨다 → ⑧ 키워드로 후보 의도를 좁힌다 → ⑨ n-gram 최근접
    → ⑩ 1·2위 차가 작으면 되묻는다 → ⑪ "무엇이 바뀌었나" 는 비교 창을 읽고, 못 읽으면 되묻는다

🔴 ② — "통신 사도 돼?" 에서 가림이 거절어를 먹었다(리뷰 R1).
🔴 ⑥ — "반도체ETF 왜 1위야" 를 못 알아보고 화면의 섹터로 조용히 답했다(재검증 R-C).
🔴 ⑪ — "9월 1일보다" 를 1영업일로 읽고 "두 달 전" 을 "적지 않았다" 고 했다(리뷰 R3 · 재검증 R-E).
   가까운 창으로 바꿔 끼우는 것도 지어내는 것이다.

## 🔒 매매어는 **권유 어미와 붙을 때만** 거절한다 (재검증 R-D)

"매수" 한 낱말로 거절하자 "외국인 순매수였어?" · "POSCO홀딩스 들어 있어?" 같은 사실 질문이 막혔다.
그래서 매매 낱말 뒤에 "해도 · 할까 · 해야 · 하는 게" 같은 **권유를 구하는 어미**가 붙어야 거절한다.
"몰빵 · 풀매수" 처럼 낱말 자체가 권유인 것만 홀로 막는다. 잘못 거절하는 비용은 낮게 뒀다(되묻기 + 버튼).

## 🔒 float 가 없다

유사도는 정수 가중치의 Dice 계수를 `Fraction` 으로 계산한다. CPU 의 SIMD 경로 · BLAS 에
따라 경계 사례가 뒤집히는 일이 구조적으로 없다 — 골든 테스트가 판정 라벨을 고정할 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from dashboard.agent.lexicon import SECTOR_TOKEN, Lexicon, normalize

__all__ = [
    "INTENTS", "INTENT_LABELS", "EXAMPLE_QUESTIONS", "REFUSAL_TEXT", "CLARIFY_TEXT",
    "MAX_CHARS", "DEFAULT_WINDOW", "MAX_WINDOW", "Route", "Window", "route", "parse_window",
]

INTENTS: tuple[str, ...] = ("why_rank", "trust", "changed", "contents")

INTENT_LABELS: Mapping[str, str] = {
    "why_rank": "왜 이 자리인가",
    "trust": "믿어도 되나",
    "changed": "무엇이 바뀌었나",
    "contents": "무엇이 들어 있나",
}

#: 질문 버튼이 채우는 문장. 🔒 이 문장들이 스스로 그 의도로 가는지 테스트가 본다.
EXAMPLE_QUESTIONS: Mapping[str, str] = {
    "why_rank": "이 섹터는 왜 이 순위야?",
    "trust": "이 순위 믿어도 돼?",
    "changed": "지난주보다 뭐가 바뀌었어?",
    "contents": "이 섹터에 뭐가 들어 있어?",
}

MAX_CHARS = 120
DEFAULT_WINDOW = 5      # "무엇이 바뀌었나" 의 기본 비교 — 한 주
MAX_WINDOW = 60         # 🔒 LOOKBACK_LONG 과 같다. 더 먼 과거는 이 도구의 창 밖이다

# ── 거절 규칙 ───────────────────────────────────────────────────────────────
# 🔴 어절 경계를 지킨다 — "사도" 는 "회사도" 에도 들어 있다. 그래서 `(?:^|\s)` 로 연다.
# 🔒 과거형 · 사실 질문은 거절하지 않는다 — "올랐어" · "매도세였어" · "진입 장벽" · "4 위".

_EOJEOL = r"(?:^|\s)"
_ADVICE = (r"(?:해도|할까|해야|하자|할래|하면\s*(?:돼|될|좋|괜찮)|하는\s*게|할\s*만|하세요|"
           r"해\s*볼까|해볼까|각|타이밍|시점)")
_NOT_RANK = r"(?!\s*(?:위|등))"      # "사 위" · "팔 위" 는 4위 · 8위 다

_TRADE = re.compile("|".join([
    r"(?:매수|매도|구매|매입|처분|손절|익절|진입|정리|보유|청산|추매|분할\s*매수|추가\s*매수)\s*" + _ADVICE,
    r"홀딩(?!스)\s*" + _ADVICE,
    r"몰빵|풀매수|올인|존버|물타기",
    r"비중\s*(?:을\s*)?(?:더\s*)?(?:늘릴|줄일|늘려|줄여|확대|축소|실어|싣)",
    r"갈아타|갈아탈",
    r"들고\s*(?:가도|갈까|있어도|있을까)",
    r"빼야|뺄까|실어도|실을까",
    _EOJEOL + r"사(?:야|도|면|자|요|지|는\s*게|고\s*싶|\s*볼까|볼까|\s" + _NOT_RANK + r"|$)",
    _EOJEOL + r"살(?:까|래|게|만|\s*만|\s" + _NOT_RANK + r"|$)",
    _EOJEOL + r"팔(?:까|아|래|면|지|자|\s" + _NOT_RANK + r"|$)",
    r"담아(?:야|라|도|볼까|둘까)|담을까|담을래|담자|담을\s*만",
    r"들어가(?:도|야|자|볼까)|들어갈까|들어갈래|들어갈\s*(?:때|만)",
    r"투자\s*(?:해도|할까|해야|하면|하자|할래|해\s*볼까)",
    r"넣을까|넣어도|넣어야",
    r"타이밍\s*(?:이야|인가|일까|맞|왔)|언제\s*(?:사|팔|들어가|매수|매도)",
    r"(?:should|can|shall)\s+i\s+(?:buy|sell|hold)|\bbuy\s+or\s+sell\b",
]))

_RECOMMEND = re.compile("|".join([
    r"추천|유망|골라\s*줘|골라줄래|찍어\s*줘",
    # 🔒 "뭐가 좋아졌어" · "뭐가 좋아서" 는 지나간 일을 묻는다 — 거절하지 않는다
    r"(?:뭐|어디|어느\s*(?:게|것|섹터))\s*가?\s*(?:더\s*)?(?:좋(?!아졌|아진|아서|았)|낫|나아(?!졌))",
    r"제일\s*좋은|최고의?\s*섹터",
]))

_FORECAST = re.compile("|".join([
    # 🔒 "오를 것" 처럼 띄어 쓴다 — 붙여 쓴 것만 막으면 평가셋이 잡는다(실제로 잡았다)
    r"오를\s*(?:까|거|것|지|래|듯|수)|오르겠|올라갈|올라가겠",
    r"떨어질|떨어지겠|내릴까|내리겠|내려갈|빠질까|빠지겠",
    r"상승할|하락할|급등할|폭락할|반등할|뛸까|뜰까|좋아질|나빠질",
    r"전망|목표\s*주?가|향후|내일|다음\s*주|다음\s*달",
    r"앞으로도|앞으로\s*(?:어떻|어때|오르|떨어|좋|나빠|계속)",
    r"계속\s*(?:1\s*(?:위|등)\s*)?(?:할까|유지\s*(?:할|될|하겠)|갈까|하겠|오를)|유지\s*(?:할까|될까|하겠)",
    r"(?:1|일)\s*(?:위|등)\s*(?:할까|하겠|할\s*수\s*있을)",
    r"(?:상승|반등|하락|오를)\s*(?:여력|가능성|여지)",
    r"수익\s*(?:날|낼)까",
]))

#: 🔒 **순서가 뜻이다** — 매매가 예측보다 먼저다. "오를 것 같은데 사도 돼?" 는 매매 질문이다.
_REFUSALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("trade", _TRADE),
    ("recommend", _RECOMMEND),
    ("forecast", _FORECAST),
)

#: "1위가 어디야" — 🔒 고른 섹터로 답하면 질문과 답이 어긋난다. 순위 표를 가리킨다
_RANKING = re.compile(
    r"(?:1|일)\s*(?:위|등)\s*(?:인|한)?\s*(?:섹터|곳|데)?\s*(?:는|가|이|은)?\s*(?:뭐|어디|어느|누구|무슨)|"
    r"(?:제일|가장)\s*높은\s*(?:섹터|곳|데)")

REFUSAL_TEXT: Mapping[str, str] = {
    "trade": "사고파는 판단은 하지 않는다. 이 도구는 지나간 데이터를 정해진 규칙으로 요약할 뿐이고, "
             "무엇을 담을지는 조가 근거를 보고 정한다.",
    "recommend": "무엇이 더 좋은지 고르지 않는다. 같은 잣대로 줄 세운 결과는 랭킹 표에 있고, "
                 "고르는 일은 조가 한다.",
    "forecast": "앞으로 오르거나 내릴지는 말하지 않는다. 이 도구에는 미래를 볼 재료가 없고, "
                "있는 척하면 그것이 곧 지어낸 값이다.",
    "compare": "두 섹터를 한 질문에서 견주지 않는다. 같은 잣대로 견준 것은 랭킹 표에 있다 — "
               "섹터 하나씩 묻는다.",
}

CLARIFY_TEXT: Mapping[str, str] = {
    "empty": "무엇을 물을지 적는다. 아래 네 질문 중 하나를 눌러도 된다.",
    "too_long": f"질문이 길다. {MAX_CHARS}자 안으로 한 가지만 묻는다.",
    "scope": "이 도구가 답하는 것은 네 가지뿐이다 — 아래에서 고른다.",
    "unknown": "무엇을 묻는지 알아듣지 못했다. 짐작해서 답하지 않는다 — 아래에서 고른다.",
    "ambiguous": "두 가지로 읽힌다. 짐작해서 답하지 않는다 — 어느 쪽인지 고른다.",
    "gics": "GICS 대분류는 여러 섹터의 묶음이다. 그 안의 섹터 하나를 골라 묻는다.",
    "ranking": "어느 섹터가 몇 위인지는 위의 순위 표 맨 위에 있다 — 섹터 하나를 골라 묻는다.",
    "sector_unclear": "섹터 이름을 확실히 읽지 못했다 — 짐작해서 다른 섹터로 답하지 않는다. "
                      "이름을 띄어 쓰거나 위에서 섹터를 고른다.",
    "window_calendar": "'며칠 전' 은 달력의 날인지 장이 열린 날인지 모른다 — "
                       "'N영업일 전' · '지난주' · '지난달' 로 묻는다.",
    "window_date": "날짜나 해 단위로는 아직 견주지 못한다 — "
                   "'N영업일 전' · '지난주' · '지난달' · '확정한 뒤로' 로 묻는다.",
    "window_long": f"견줄 수 있는 것은 최근 {MAX_WINDOW}영업일까지다 — 더 먼 과거는 이 도구의 창 밖이다.",
    "window_unknown": "견줄 때를 정확히 읽지 못했다 — 짐작해서 견주지 않는다. "
                      "'N영업일 전' · '지난주' · '지난달' · '확정한 뒤로' 로 묻는다.",
}

# ── 키워드 — 후보를 좁힌다 ─────────────────────────────────────────────────

_KEYWORDS: Mapping[str, re.Pattern[str]] = {
    "why_rank": re.compile(
        r"왜|이유|어째서|까닭|순위|몇\s*위|\d+\s*(?:위|등)|등수|점수|설명|이\s*자리|높|낮"),
    "trust": re.compile(
        r"믿|신뢰|확실|정말|진짜|괜찮|위험|약점|반론|허점|흔들|조심|주의|리스크|함정|의심|맞아|맞나|정확"),
    "changed": re.compile(
        r"바뀌|바꼈|달라|변화|변했|변동|전보다|지난\s*주|지난\s*달|어제|요즘|이번\s*주|추이|"
        r"올랐|떨어졌|내려갔|밀렸|올라왔|좋아졌|나빠졌|확정\s*(?:한\s*)?(?:뒤|후|이후|때|부터)|"
        r"전이랑|전과"),
    "contents": re.compile(
        r"들어\s*있|들어가\s*있|들었|구성|종목|etf|포함|담겨|편입|이뤄|이루어|어떤\s*회사|"
        r"무슨\s*회사|뭐\s*가?\s*있|무엇으로|묶었|묶여"),
}

# ── 예문 — n-gram 최근접의 기준 ────────────────────────────────────────────
# 🔒 섹터 이름 자리는 `SECTOR_TOKEN`("섹터") 으로 적는다 — 질문도 같은 낱말로 가려진다.
# 🔒 거절 예문을 두지 않는다. 거절은 유사도로 하지 않는다(머리주석).

_EXAMPLES: Mapping[str, tuple[str, ...]] = {
    "why_rank": (
        "섹터 왜 1위야", "섹터는 왜 이 순위야", "섹터 점수가 왜 이렇게 높아",
        "섹터 순위가 왜 낮아", "이 자리에 있는 이유가 뭐야", "왜 이 점수가 나왔어",
        "섹터 몇 위야 이유 설명해줘", "무엇이 순위를 끌어올렸어",
    ),
    "trust": (
        "섹터 순위 믿어도 돼", "이 점수 믿을 만해", "섹터 순위 확실해",
        "이 결과 괜찮은 거야", "약점이 뭐야", "조심할 점 있어",
        "이 순위 흔들리지 않아", "반론은 없어",
    ),
    "changed": (
        "지난주보다 뭐가 바뀌었어", "섹터 어제랑 뭐가 달라졌어", "요즘 순위 변화 어때",
        "지난달이랑 비교하면 뭐가 변했어", "확정한 뒤로 뭐가 달라졌어", "섹터 순위 올랐어",
        "섹터 점수 떨어졌어", "최근 추이가 어때",
    ),
    "contents": (
        "섹터에 뭐가 들어 있어", "섹터 구성 종목 알려줘", "어떤 etf 로 되어 있어",
        "섹터는 어떤 회사들로 이뤄졌어", "섹터에 포함된 종목이 뭐야", "섹터 etf 몇 개야",
        "무엇으로 묶었어", "섹터 정의가 뭐야",
    ),
}

#: 이 아래면 무엇인지 모른다. 🔒 `Fraction` — float 가 아니다.
_FLOOR = Fraction(1, 4)
#: 1·2위 차가 이보다 작으면 되묻는다.
_MARGIN = Fraction(1, 20)


def _grams(text: str) -> dict[str, int]:
    """문자 1~3-gram → 가중치(= 길이). 🔒 긴 조각이 더 무겁다 — "믿어도" 가 "도" 보다 뜻이 크다."""
    padded = "_" + "_".join(text.split()) + "_"
    out: dict[str, int] = {}
    for n in (1, 2, 3):
        for i in range(len(padded) - n + 1):
            gram = padded[i:i + n]
            if gram.strip("_"):
                out[gram] = n
    return out


def _dice(a: Mapping[str, int], b: Mapping[str, int]) -> Fraction:
    total = sum(a.values()) + sum(b.values())
    if total == 0:
        return Fraction(0)
    shared = sum(weight for gram, weight in a.items() if gram in b)
    return Fraction(2 * shared, total)


_EXAMPLE_GRAMS = {intent: tuple(_grams(normalize(e)) for e in examples)
                  for intent, examples in _EXAMPLES.items()}


@dataclass(frozen=True, slots=True)
class Window:
    """비교 창. 🔒 `problem` 이 있으면 답하지 않고 되묻는다."""

    days: int
    since_confirm: bool
    source: str                 # 확정 · 영업일 · 주 · 달 · 어제 · 지난주 · 지난달 · 기본
    problem: str | None = None  # window_calendar · window_date · window_long · window_unknown


@dataclass(frozen=True, slots=True)
class Route:
    """라우터의 답. 🔒 `kind` 는 셋뿐이다 — 답한다 · 거절한다 · 되묻는다."""

    kind: str                           # "answer" | "refuse" | "clarify"
    reason: str
    intent: str | None = None
    sector_id: str | None = None        # 질문에 **적힌** 섹터. 없으면 None
    gics: str | None = None
    candidates: tuple[str, ...] = ()
    window_days: int | None = None
    since_confirm: bool = False
    window_source: str = ""
    matched: tuple[str, ...] = ()       # 설명용 — 걸린 규칙 · 키워드


_KO_COUNT: Mapping[str, int] = {"한": 1, "두": 2, "세": 3, "석": 3, "네": 4, "넉": 4, "다섯": 5, "여섯": 6}


def _capped(days: int, source: str) -> Window:
    if days < 1:
        return Window(DEFAULT_WINDOW, False, source, "window_unknown")
    if days > MAX_WINDOW:
        return Window(DEFAULT_WINDOW, False, source, "window_long")
    return Window(days, False, source)


def parse_window(text: str) -> Window:
    """"무엇이 바뀌었나" 의 비교 창.

    🔒 숫자는 **영업일 · 거래일 · 주 · 달** 로만 받는다(주 5영업일 · 달 20영업일 — 답은 실제 기준일을 날짜로
       적는다). 한글 수(두 달 · 석 달)도 읽는다. 달력일("N일" · 이틀 · 며칠) · 날짜 · 해 단위 · 창 초과 ·
       알아듣지 못한 기간 표현("반", "확정 전")은 **되묻는다.** 기간 표현이 전혀 없을 때만 기본 창이다.
    """
    if re.search(r"확정\s*(?:하기\s*)?전", text):
        return Window(DEFAULT_WINDOW, False, "확정 전", "window_unknown")
    if re.search(r"확정\s*(?:한\s*)?(?:뒤|후|이후|때|부터|하고|당시)|확정했을\s*때", text):
        return Window(DEFAULT_WINDOW, True, "확정")
    if re.search(r"\d{1,2}\s*월\s*\d{1,2}\s*일|\d{1,2}\s*월|\d+\s*년|작년|재작년|올해|연초|연말|"
                 r"분기|반년|\d{1,2}\s*/\s*\d{1,2}", text):
        return Window(DEFAULT_WINDOW, False, "날짜", "window_date")
    if re.search(r"(?:달|주|개월)\s*반", text):
        return Window(DEFAULT_WINDOW, False, "반", "window_unknown")
    business = re.search(r"(\d{1,4})\s*(?:영업일|거래일)", text)
    if business:
        return _capped(int(business.group(1)), "영업일")
    weeks = re.search(r"(\d{1,3})\s*주", text)
    if weeks:
        return _capped(int(weeks.group(1)) * 5, "주")
    months = re.search(r"(\d{1,3})\s*(?:달|개월)", text)
    if months:
        return _capped(int(months.group(1)) * 20, "달")
    spoken = re.search(r"(다섯|여섯|한|두|세|석|네|넉)\s*(주|달)", text)
    if spoken:
        unit = 5 if spoken.group(2) == "주" else 20
        return _capped(_KO_COUNT[spoken.group(1)] * unit, "주" if unit == 5 else "달")
    if re.search(r"지지난\s*주", text):
        return Window(10, False, "지난주")
    if re.search(r"지지난\s*달", text):
        return Window(40, False, "지난달")
    if re.search(r"\d{1,4}\s*일|이틀|사흘|나흘|열흘|보름|그저께|그제|엊그제|며칠", text):
        return Window(DEFAULT_WINDOW, False, "달력일", "window_calendar")
    if re.search(r"어제|전일|하루", text):
        return Window(1, False, "어제")
    if re.search(r"지난\s*달|저번\s*달|전월", text):
        return Window(20, False, "지난달")
    if re.search(r"지난\s*주|이번\s*주|일주일|저번\s*주|전주", text):
        return Window(5, False, "지난주")
    if re.search(r"전\s*(?:보다|이랑|과|하고|대비|에\s*비해)|대비|이전", text):
        return Window(DEFAULT_WINDOW, False, "모름", "window_unknown")
    return Window(DEFAULT_WINDOW, False, "기본")


def _refusal(text: str) -> Route | None:
    padded = f" {text} "
    for code, pattern in _REFUSALS:
        hit = pattern.search(padded)
        if hit:
            return Route(kind="refuse", reason=code, matched=(hit.group(0).strip(),))
    return None


def route(question: object, lexicon: Lexicon) -> Route:
    """질문 하나를 가른다. 🔒 순수 함수다 — 같은 질문 · 같은 사전이면 같은 답이다."""
    text = normalize(question)
    if not text:
        return Route(kind="clarify", reason="empty")
    if len(text) > MAX_CHARS:
        return Route(kind="clarify", reason="too_long")

    refused = _refusal(text)                       # ② 가리기 전
    if refused is not None:
        return refused
    found = lexicon.find(text)
    masked = found.masked
    refused = _refusal(masked)                     # ④ 가린 뒤
    if refused is not None:
        return refused
    if len(found.sectors) >= 2:
        return Route(kind="refuse", reason="compare", candidates=found.sectors)
    if found.unclear:
        return Route(kind="clarify", reason="sector_unclear", candidates=found.unclear)

    sector_id = found.sectors[0] if found.sectors else None
    if sector_id is None and found.gics:
        return Route(kind="clarify", reason="gics", gics=found.gics[0])
    if sector_id is None and _RANKING.search(masked):
        return Route(kind="clarify", reason="ranking")

    hits = tuple(intent for intent in INTENTS if _KEYWORDS[intent].search(masked))
    if not hits and sector_id is None and SECTOR_TOKEN not in masked.split():
        return Route(kind="clarify", reason="scope")

    pool = hits or INTENTS
    grams = _grams(masked)
    scored = sorted(
        ((max(_dice(grams, g) for g in _EXAMPLE_GRAMS[intent]), intent) for intent in pool),
        key=lambda pair: (-pair[0], INTENTS.index(pair[1])),
    )
    if len(hits) == 1:
        best_intent, reason = hits[0], "keyword"
    else:
        best_score, best_intent = scored[0]
        if best_score < _FLOOR:
            return Route(kind="clarify", reason="unknown", sector_id=sector_id)
        if len(scored) > 1 and best_score - scored[1][0] < _MARGIN:
            return Route(kind="clarify", reason="ambiguous", sector_id=sector_id,
                         candidates=tuple(sorted((scored[0][1], scored[1][1]),
                                                 key=INTENTS.index)))
        reason = "ngram"

    if best_intent != "changed":
        return Route(kind="answer", reason=reason, intent=best_intent, sector_id=sector_id,
                     matched=hits)
    window = parse_window(text)
    if window.problem is not None:
        return Route(kind="clarify", reason=window.problem, sector_id=sector_id)
    return Route(kind="answer", reason=reason, intent=best_intent, sector_id=sector_id,
                 window_days=window.days, since_confirm=window.since_confirm,
                 window_source=window.source, matched=hits)
