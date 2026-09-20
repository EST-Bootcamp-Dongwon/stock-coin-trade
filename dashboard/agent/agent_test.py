"""내부 에이전트 엔진(Layer A)의 계약.

## 무엇을 고정하나

1. **라우터** — 질문 평가셋. 🔴 거절 세트(매매 · 예측 · 추천)와 **잘못 거절하면 안 되는 세트**
   ("회사도" · "올랐어" · "순매수였어" · "POSCO홀딩스")를 함께 둔다. 모델을 들이려면 이 표에서 이겨야 한다
   (ADR-SC-0013 ①). 섹터 이름을 못 읽으면 짐작하지 않고 되묻는지, 비교 창을 못 읽으면 되묻는지도 본다.
2. **불변식** — Gap 여섯 칸 · 구조적 비공시의 증거 · 방어의 세기 상한 · 장부에 float 금지 · 자리 모양.
3. **guard** — 모든 섹터 × 프리셋 × 질문이 통과하고, **일부러 망가뜨린 브리프는 전부 잡는다.**
   통과만 보는 guard 테스트는 아무것도 막지 않는 guard 와 구별되지 않는다. 적대적 리뷰와 재검증(2026-09-14)이
   통과시킨 변조를 하나씩 여기 옮겼다 — 자리 맞바꾸기 · 같은 단위 근거 바꿔치기 · 부호 떼기 · 역할 내리기 ·
   해석과 방향이 따로 노는 문장 · 산문 끼우기 · 문장 더하고 빼기 · 빈칸 지우기 · 근거 지우기 · 다른 축 상수 ·
   한계 지우기 · 판정 뒤집기 · "None개" · 한글 수사.
4. **골든** — 브리프 모양. 🔒 `--snapshot-update` 는 문구가 바뀐 이유를 설명할 수 있을 때만.
5. **정적 검사** — 에이전트는 읽기만 하고, 점수 경로는 에이전트를 모르며, GIC 원문 문장이 저장소에 없다.
6. **화면** — 질문칸 · 거절 · guard 실패 시 문장을 버리는 것 · 모달에는 질문칸이 없는 것 · 섹터를 바꾸면
   질문칸이 비는 것 · 같은 질문을 다시 그려도 빈칸 상태가 바뀌지 않는 것.

🔒 실제 KRX 데이터를 픽스처로 쓰지 않는다 — 합성 표다(AGENTS.md 5장). 실데이터 연기 시험은
   로컬 파생 파일이 있을 때만 돌고 아무것도 저장하지 않는다.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pandas as pd
import pytest

from dashboard import explain, view
from dashboard.agent import compose, engine, guard, ids, intent, redteam, slots, templates
from dashboard.agent.inventory import Evidence, Gap, collect, gaps_for
from dashboard.agent.lexicon import ALIASES, SECTOR_TOKEN, Lexicon, normalize
from dashboard.agent.redteam import Attack
from dashboard.dashboard_test import _as_member, _confirm, _open_as, _ranking_page, _teams_page, ledger  # noqa: F401
from sector.scoring import AXES, PRESETS
from sector.sector_master import GicsSector, Instrument, Sector, SectorMaster
from sector.workspace.fold import Comment, Team, Workspace

ROOT = Path(__file__).resolve().parents[2]

# ── 합성 데이터 ─────────────────────────────────────────────────────────────

SECTORS = ("alpha", "beta", "gamma", "delta", "omega", "zeta")
NAMES_KO = {"alpha": "알파", "beta": "2차베타", "gamma": "감마", "delta": "델타", "omega": "오메가",
            "zeta": "제타"}
DAYS = tuple(f"202608{d:02d}" for d in range(1, 26))


def _master() -> SectorMaster:
    sectors = tuple(
        Sector(id=sid, name_ko=NAMES_KO[sid], gics="Information Technology",
               note=f"{NAMES_KO[sid]} 을 이렇게 묶은 합성 근거다 — 테스트 전용이다",
               etfs=tuple(Instrument(code=f"9{i}999A", name=f"합성 ETF {i}00")
                          for i in range(1, (1 if sid == "beta" else 2) + 1)),
               members=tuple(Instrument(code=f"00{i}{j}00", name=f"합성종목{j}")
                             for j in range(3)),
               liquidity_warning=sid in ("alpha", "delta"))
        for i, sid in enumerate(SECTORS))
    return SectorMaster(
        version="t", source_notice="한국거래소 통계정보",
        gics_sectors=(GicsSector("Information Technology", "정보기술"),
                      GicsSector("Energy", "에너지", empty_reason="합성 — 섹터가 없다")),
        sectors=sectors, config_sha256="x")


def make_frame() -> pd.DataFrame:
    """🔒 가지를 전부 밟게 짠다 — 결측 축(omega) · 강등 축(gamma) · ETF 1종(beta) ·
    유동성 미달(delta) · 유동성 모름(omega) · 순위 이력 구멍(omega, 열째 날) ·
    🔴 **서로 어긋난 빈 값**(zeta 마지막 날: ETF 수 · 쓴 축 수가 비고, 밸류 σ 가 비었는데 결측 축은 빈 글자)."""
    rows = []
    last = len(DAYS) - 1
    for d, day in enumerate(DAYS):
        for i, sid in enumerate(SECTORS):
            if sid == "omega" and d == 9:
                continue                                   # 이력 구멍
            z = {
                "M": (i - 2) * 6000 + (d * 150 if i == 0 else -d * 90 if i == 1 else d * 20),
                "F": (2 - i) * 4000 + d * 30,
                "B": ((i * 7 + d) % 5 - 2) * 3000,
                "V": -(i - 1) * 7000 - (d * 100 if i == 3 else 0),
            }
            if sid == "omega" and d >= last - 2:
                z["V"] = None
            if sid == "zeta" and d == last:
                z["V"] = None
            row = {"bas_dd": day, "sector_id": sid, "gics": "Information Technology",
                   "n_axes_used": sum(1 for a in AXES if z[a] is not None),
                   "axes_missing": "".join(a for a in AXES if z[a] is None),
                   "axes_degraded": "B" if sid == "gamma" else "",
                   "liquidity_ok": (False if sid == "delta"
                                    else None if (sid == "omega" and d == last) else True),
                   "etf_n": 1 if sid == "beta" else 2, "is_partial": False,
                   "config_version": "t", "config_sha256": "x", "fetched_at": "t"}
            if sid == "zeta" and d == last:
                row.update(etf_n=None, n_axes_used=None, axes_missing="")
            for a in AXES:
                row[f"{a.lower()}_z_bp"] = z[a]
                row[f"{a.lower()}_raw_bp"] = None if z[a] is None else z[a] // 7 + 13
            for p, w in PRESETS.items():
                live = [a for a in AXES if z[a] is not None]
                row[f"score_{p}_bp"] = round(sum(z[a] * w[a] for a in live) / sum(w[a] for a in live))
            rows.append(row)
    frame = pd.DataFrame(rows)
    for p in PRESETS:
        frame[f"rank_{p}"] = (frame.groupby("bas_dd")[f"score_{p}_bp"]
                              .rank(ascending=False, method="min").astype("Int64"))
    for column in [c for c in frame.columns if c.endswith("_bp")] + ["n_axes_used", "etf_n"]:
        frame[column] = frame[column].astype("Int64")
    return frame


FRAME = make_frame()
MASTER = _master()
NAMES = view.Names.of(MASTER)


def _workspace(confirmed_at: str | None = "2026-08-20T16:30:00+00:00") -> Workspace:
    team = Team(id="t1", name="A조", created_by="동원", created_at="2026-08-01T00:00:00+00:00",
                members=("동원",), core_sector="alpha" if confirmed_at else None,
                core_reason="합성 사유" if confirmed_at else None, confirmed_at=confirmed_at,
                confirmed_by="동원" if confirmed_at else None)
    comments = (Comment(event_id="e1", team_id="t1", author="동원", body="합성",
                        at="2026-08-02T00:00:00+00:00", sector_id="alpha"),)
    return Workspace(teams={"t1": team}, comments=comments)


def _brief(intent_name: str, sector_id: str = "alpha", *, profile: str = "balanced", **kw) -> engine.Answer:
    return engine.brief_for(intent_name, frame=FRAME, sector_id=sector_id, names=NAMES,
                            master=MASTER, profile=profile, **kw)


def _ask(question: str, context: str | None = "alpha", **kw) -> engine.Answer:
    return engine.answer(question, frame=FRAME, context_sector=context, names=NAMES,
                         master=MASTER, **kw)


# ── 1. 사전 ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def real_master():
    from sector.sector_master import load

    return load()


def test_별칭은_실재하는_섹터를_가리킨다(real_master):
    known = {s.id for s in real_master.sectors}
    assert set(ALIASES) <= known, set(ALIASES) - known


def test_별칭이_두_섹터에_걸리지_않는다(real_master):
    """🔴 짧은 별칭이 다른 섹터 별칭 안에 들어 있으면 긴 것부터 찾아도 엉뚱하게 걸릴 수 있다."""
    owners: dict[str, set[str]] = {}
    for sector in real_master.sectors:
        for alias in (sector.name_ko, sector.id, *ALIASES.get(sector.id, ())):
            owners.setdefault("".join(normalize(alias).split()), set()).add(sector.id)
    assert all(len(v) == 1 for v in owners.values()), {k: v for k, v in owners.items() if len(v) > 1}
    for a, sa in owners.items():
        for b, sb in owners.items():
            if a != b and a in b:
                assert sa == sb, f"'{a}'({sa}) 가 '{b}'({sb}) 안에 들어 있다"


@pytest.mark.parametrize("question,sectors,unclear", [
    ("2차 전지 순위 믿을 만해?", ("battery",), ()),
    ("석유화학 왜 1위야", ("chemical",), ()),
    ("반도체주가 왜 1위야", ("semiconductor",), ()),
    ("반도체ETF 왜 1위야", ("semiconductor",), ()),          # 🔴 재검증 R-C
    ("조선업체 믿어도 돼?", ("shipbuilding",), ()),
    ("2차전지주들 왜 높아", ("battery",), ()),
    ("반도체들 믿어도 돼?", ("semiconductor",), ()),
    ("통신사 왜 1위야", ("telecom",), ()),
    ("KODEX반도체 왜 1위야", (), ("semiconductor",)),       # 🔴 읽지 못하면 애매하다고 돌려준다
    ("유통기한 같은 게 있어?", (), ("retail_ecommerce",)),  # 🔴 낱말 안에서 섹터로 잡지 않는다(리뷰 O7)
    ("제약 조건 없이 왜 1위야", (), ()),
])
def test_섹터_이름은_어절_머리에서_찾고_애매하면_애매하다고_한다(real_master, question, sectors, unclear):
    found = Lexicon.of(real_master).find(normalize(question))
    assert (found.sectors, found.unclear) == (sectors, unclear), found
    if sectors:
        assert any(word.startswith(SECTOR_TOKEN) for word in found.masked.split())


def test_GICS_이름은_테마_섹터가_없을_때만_잡는다(real_master):
    lexicon = Lexicon.of(real_master)
    assert lexicon.find(normalize("금융 왜 높아")).gics == ("Financials",)
    healthcare = lexicon.find(normalize("헬스케어 왜 높아"))
    assert healthcare.sectors == ("healthcare",) and healthcare.gics == ()


def test_섹터_정의가_없으면_빈_사전이다():
    assert Lexicon.of(None).find("반도체 왜 1위야").sectors == ()


def test_폭_없는_문자는_지운다():
    """🔴 "매(폭 없는 문자)수" 가 "매 수" 가 되어 거절을 비껴갔다(리뷰 R1)."""
    assert normalize("매" + chr(0x200B) + "수") == "매수"


# ── 2. 라우터 — 질문 평가셋 ─────────────────────────────────────────────────

ROUTES = [
    # 답한다
    ("반도체 왜 1위야?", "answer", "why_rank"),
    ("이 섹터는 왜 이 순위야", "answer", "why_rank"),
    ("점수가 왜 이렇게 낮아", "answer", "why_rank"),
    ("조선 몇 위야", "answer", "why_rank"),
    ("무엇이 순위를 끌어올렸어", "answer", "why_rank"),
    ("반도체 진입 장벽 설명해줘", "answer", "why_rank"),
    ("이 순위 믿어도 돼?", "answer", "trust"),
    ("방산 약점이 뭐야", "answer", "trust"),
    ("조심할 거 있어?", "answer", "trust"),
    ("2차 전지 순위 믿을 만해?", "answer", "trust"),
    ("지난주보다 뭐가 바뀌었어", "answer", "changed"),
    ("어제보다 뭐가 올랐어", "answer", "changed"),
    ("어제 매도세였어?", "answer", "changed"),
    ("확정한 뒤로 뭐가 달라졌어", "answer", "changed"),
    ("요즘 순위 변화 어때", "answer", "changed"),
    ("뭐가 좋아졌어?", "answer", "changed"),
    ("반도체 떨어졌어?", "answer", "changed"),
    ("10영업일 전보다 뭐가 바뀌었어", "answer", "changed"),
    ("반도체 두 달 전보다 뭐가 바뀌었어", "answer", "changed"),
    ("지지난주보다 뭐가 바뀌었어", "answer", "changed"),
    ("조선에 뭐 들어있어?", "answer", "contents"),
    ("이거 어떤 회사들로 이뤄졌어", "answer", "contents"),
    ("ETF 몇 개야", "answer", "contents"),
    ("회사도 많네 구성 알려줘", "answer", "contents"),
    ("사업 구성이 뭐야", "answer", "contents"),
    ("회사도 들어 있어?", "answer", "contents"),
    ("POSCO홀딩스 들어 있어?", "answer", "contents"),
    # 거절 — 매매
    ("삼성전자 사야 돼?", "refuse", "trade"),
    ("왜 반도체 안 사?", "refuse", "trade"),
    ("사지 말까", "refuse", "trade"),
    ("살까요", "refuse", "trade"),
    ("순위 믿고 사도 돼?", "refuse", "trade"),
    ("지금 들어가도 돼?", "refuse", "trade"),
    ("비중 늘릴까", "refuse", "trade"),
    ("손절해야 해?", "refuse", "trade"),
    ("팔까 말까", "refuse", "trade"),
    ("어디에 투자할까", "refuse", "trade"),
    ("오를 것 같은데 사도 돼?", "refuse", "trade"),
    # 🔴 리뷰 R1 — 가림이 거절어를 먹었다 · 규칙에 없던 동사
    ("통신 사도 돼?", "refuse", "trade"),
    ("증권 사야 돼?", "refuse", "trade"),
    ("통신 사자", "refuse", "trade"),
    ("반도체 몰빵해도 돼?", "refuse", "trade"),
    ("구매해도 돼?", "refuse", "trade"),
    ("매입해도 돼?", "refuse", "trade"),
    ("보유해도 돼?", "refuse", "trade"),
    ("계속 들고 가도 돼?", "refuse", "trade"),
    ("비중 더 실어도 돼?", "refuse", "trade"),
    ("반도체 사는 게 맞아?", "refuse", "trade"),
    ("반도체 정리할까", "refuse", "trade"),
    ("사볼까", "refuse", "trade"),
    ("갈아탈까", "refuse", "trade"),
    ("매" + chr(0x200B) + "수해도 돼?", "refuse", "trade"),
    ("should i buy", "refuse", "trade"),
    # 거절 — 추천
    ("반도체 추천해줘", "refuse", "recommend"),
    ("뭐가 제일 좋은 섹터야", "refuse", "recommend"),
    ("어느 섹터가 나아?", "refuse", "recommend"),
    ("반도체랑 조선 뭐가 나아?", "refuse", "recommend"),
    # 거절 — 예측
    ("내일 오를까?", "refuse", "forecast"),
    ("반도체 오를 것 같아?", "refuse", "forecast"),
    ("다음 주 전망 어때", "refuse", "forecast"),
    ("앞으로 어떻게 될까", "refuse", "forecast"),
    ("반도체 떨어질까", "refuse", "forecast"),
    ("반도체 상승 여력 있어?", "refuse", "forecast"),
    ("반도체 앞으로도 1위 할까?", "refuse", "forecast"),
    ("계속 1위 유지할까?", "refuse", "forecast"),
    # 거절 — 두 섹터
    ("반도체랑 조선 왜 순위 차이 나", "refuse", "compare"),
    ("반도체에서는 조선보다 왜 높아", "refuse", "compare"),   # 🔴 재검증 R-C
    # 되묻는다
    ("", "clarify", "empty"),
    ("가" * (intent.MAX_CHARS + 1), "clarify", "too_long"),
    ("날씨 어때", "clarify", "scope"),
    ("금융 왜 높아", "clarify", "gics"),
    ("1등 섹터 뭐야?", "clarify", "ranking"),
    ("KODEX반도체 왜 1위야", "clarify", "sector_unclear"),
    # 🔴 리뷰 R3 · 재검증 R-E — 비교 창을 잘못 읽고 조용히 바꿔 끼웠다
    ("반도체 9월 1일보다 뭐가 바뀌었어", "clarify", "window_date"),
    ("작년보다 뭐가 바뀌었어", "clarify", "window_date"),
    ("10일 전보다 뭐가 바뀌었어", "clarify", "window_calendar"),
    ("이틀 전보다 뭐가 바뀌었어", "clarify", "window_calendar"),
    ("100영업일 전보다 뭐가 바뀌었어", "clarify", "window_long"),
    ("2달 반 전보다 뭐가 바뀌었어", "clarify", "window_unknown"),
    ("확정 전이랑 뭐가 달라", "clarify", "window_unknown"),
]


@pytest.mark.parametrize("question,kind,expected", ROUTES, ids=[r[0][:20] or "빈칸" for r in ROUTES])
def test_질문_평가셋(real_master, question, kind, expected):
    route = intent.route(question, Lexicon.of(real_master))
    assert route.kind == kind, route
    assert (route.intent if kind == "answer" else route.reason) == expected, route


@pytest.mark.parametrize("question", [
    "POSCO홀딩스 들어 있어?", "철강에 포스코홀딩스 포함돼?", "반도체는 뭐가 좋아서 1위야?",
    "지난주보다 뭐가 좋았어?", "반도체는 왜 1위일까?", "1위 할 수 있었던 이유가 뭐야?",
    "모멘텀 수익률 몇 %야?", "지난주 내내 1위 계속 유지했어?", "밸류 가중치 내리면 몇 위야?",
    "왜 오르면 점수가 올라?", "외국인 순매수였어?", "기관 매수 많았어?", "매도 압력 컸어?",
    "반도체 ETF 비중 줄었어?", "반도체 사 위야?", "조선 팔 위였어?", "처분 공시 반영돼?",
    "타이밍 지표도 봐?", "hold 가 뭐야?", "원전 사업 구성 알려줘", "증권사 도움말", "사이클이 뭐야",
])
def test_사실을_묻는_질문은_거절하지_않는다(real_master, question):
    """🔴 매매 낱말 하나로 거절하자 사실 질문이 막혔다(재검증 R-D) — 권유 어미가 붙어야 거절한다."""
    assert intent.route(question, Lexicon.of(real_master)).kind != "refuse", question


def test_예시_질문은_제_의도로_간다(real_master):
    lexicon = Lexicon.of(real_master)
    for name, question in intent.EXAMPLE_QUESTIONS.items():
        assert intent.route(question, lexicon).intent == name, question


def test_라우터는_float_를_쓰지_않는다():
    """🔒 유사도는 `Fraction` 이다 — CPU 가 달라도 경계 사례가 뒤집히지 않는다."""
    source = (ROOT / "dashboard" / "agent" / "intent.py").read_text(encoding="utf-8")
    assert not re.search(r"\bfloat\(|import numpy|import math", source)


@pytest.mark.parametrize("text,days,since,problem", [
    ("어제랑 뭐가 달라", 1, False, None), ("지난주보다", 5, False, None),
    ("지난달이랑", 20, False, None), ("10영업일 전보다", 10, False, None),
    ("2주 전보다", 10, False, None), ("3달 전", 60, False, None),
    ("두 달 전보다", 40, False, None), ("석 달 전", 60, False, None),
    ("지지난주보다", 10, False, None), ("전주 대비", 5, False, None),
    ("확정한 뒤로", intent.DEFAULT_WINDOW, True, None),
    ("뭐가 바뀌었어", intent.DEFAULT_WINDOW, False, None),
    ("10일 전보다", intent.DEFAULT_WINDOW, False, "window_calendar"),
    ("이틀 전보다", intent.DEFAULT_WINDOW, False, "window_calendar"),
    ("보름 전", intent.DEFAULT_WINDOW, False, "window_calendar"),
    ("9월 1일보다", intent.DEFAULT_WINDOW, False, "window_date"),
    ("1년 전보다", intent.DEFAULT_WINDOW, False, "window_date"),
    ("100영업일 전", intent.DEFAULT_WINDOW, False, "window_long"),
    ("4달 전", intent.DEFAULT_WINDOW, False, "window_long"),
    ("2달 반 전", intent.DEFAULT_WINDOW, False, "window_unknown"),
    ("0영업일 전", intent.DEFAULT_WINDOW, False, "window_unknown"),
    ("확정 전이랑", intent.DEFAULT_WINDOW, False, "window_unknown"),
])
def test_비교_창을_읽고_못_읽으면_되묻는다(text, days, since, problem):
    window = intent.parse_window(normalize(text))
    assert (window.days, window.since_confirm, window.problem) == (days, since, problem)


# ── 3. 불변식 ───────────────────────────────────────────────────────────────

def test_구조적_비공시는_증거가_있어야_한다():
    with pytest.raises(ValueError, match="증거"):
        Gap(id="GP-INVESTOR", item="x", kind="구조적 비공시", why="x", needed="x",
            workaround="x", affects="x", basis="  ")


def test_Gap_은_여섯_칸이_비면_만들어지지_않는다():
    with pytest.raises(ValueError, match="여섯 칸"):
        Gap(id="GP-PAST", item="x", kind="미확보", why="", needed="x", workaround="x", affects="x")


def test_대장에_없는_Gap_ID_는_만들어지지_않는다():
    with pytest.raises(ValueError, match="대장"):
        Gap(id="GP-NOPE", item="x", kind="미확보", why="x", needed="x", workaround="x", affects="x")


def test_방어의_세기에_상한이_있다():
    base = dict(id="AT-1", target="TH-RANK", defense_template="x", defense="x")
    with pytest.raises(ValueError, match="숫자로 된 근거"):
        Attack(**base, case="pass", cites=())
    with pytest.raises(ValueError, match="판정을 미루고"):
        Attack(**base, case="weak", cites=())
    with pytest.raises(ValueError, match="Gap"):
        Attack(**base, case="open", cites=())


def test_장부에_float_를_넣지_않는다():
    with pytest.raises(ValueError, match="float"):
        Evidence("EV-X", "x", 1.5, "int", "거래소 파생", "상", ("column", "x"))


def test_근거를_달지_않은_문장은_만들어지지_않는다():
    with pytest.raises(ValueError, match="근거"):
        compose.Sentence("since", "말.", "말.")


def test_고정_ID_대장이_접두사를_지키고_은퇴한_ID_를_되살리지_않는다():
    assert all(k.split("-")[0] in ("TH", "RK", "AT", "GP") for k in ids.CATALOG)
    assert not (ids.RETIRED & set(ids.CATALOG))
    # 🔒 이 도구에 없는 단계의 접두사는 쓰지 않는다
    assert not any(k.startswith(("IP-", "CT-", "CD-")) for k in ids.CATALOG)


@pytest.mark.parametrize("z", [-25000, -20000, -19999, -10000, -9999, -4000, -3999, 0, 3999, 4000,
                               9999, 10000, 19999, 20000, 25000])
def test_에이전트와_설명화면의_총점_문장이_글자까지_같다(z):
    """🔒 옛 테스트는 두 쪽의 **구간 낱말**을 대조했다. 그 낱말은 사라졌지만
    (이슈 #17 ① · ADR-SC-0020) **대조 자체가 요점이었다** — 렌더러 둘이 같은 문장을
    말해야 guard 가 «문장이 다르다» 를 결함으로 읽을 수 있다. 그래서 대조 대상을
    문장 전체로 옮긴다.
    """
    assert slots.fill(templates.SIGMA, {"EV-SCORE": z}) == f"점수는 {z / 10000:+.2f}σ 다."


def test_총점_문장에는_검사할_방향_낱말이_없다():
    """🔴 방향 낱말을 문장에서 지웠으면 **guard 의 표에서도** 지워야 한다.

    남겨 두면 «어느 문장도 쓰지 않는 낱말» 이 허용 목록에 남고, 다음 사람이 총점에
    구간 낱말을 다시 붙여도 guard 가 통과시킨다 — ADR-SC-0013 이 «새 방향 낱말을
    더하면 표도 함께 더한다» 고 적은 것의 **반대 방향**이다.
    """
    assert "sigma" not in guard._ROLE_WORDS
    assert templates.ROLE_OF["sigma"] == "plain"
    for banned in ("높다", "낮다", "비슷하다", "뚜렷이", "다소"):
        assert banned not in templates.SIGMA
    assert not hasattr(templates, "sigma_band")


def test_자리는_빈_값을_글자로_만들지_않는다():
    with pytest.raises(slots.SlotError, match="비어"):
        slots.fill("ETF 는 ⟦EV-ETF-N|int⟧개다.", {"EV-ETF-N": None})
    assert slots.fill("⟦EV-A|sigma⟧ · ⟦EV-A|pct2⟧ · ⟦EV-B|date⟧", {"EV-A": -1234, "EV-B": "20260801"}) \
        == "-0.12 · 12.34 · 2026-08-01"


def test_모든_문장_열쇠에_역할이_있다():
    assert set(templates.ROLE_OF.values()) >= {"plain", "fixed", "disclaimer", "axis_plain"}


# ── 4. guard — 전부 통과하고, 망가뜨리면 잡는다 ────────────────────────────

def _all_answers():
    workspace = _workspace()
    for sid in SECTORS:
        for profile in PRESETS:
            for name in intent.INTENTS:
                yield _brief(name, sid, profile=profile)
        yield _brief("changed", sid, window_days=1)
        yield _brief("changed", sid, window_days=20)
        yield _brief("changed", sid, workspace=workspace, team_id="t1", since_confirm=True)
        yield _brief("changed", sid, window_source="기본")
        yield _brief("why_rank", sid, days=30)                 # 짧은 이력 한계
        yield _brief("contents", sid, workspace=workspace, team_id="t1")


def test_모든_섹터_프리셋_질문이_guard_를_통과한다():
    for answer in _all_answers():
        assert answer.brief is not None, answer.message
        assert answer.violations == (), (answer.brief.sector_id, answer.brief.intent,
                                         answer.violations)


def test_브리프가_쓰는_ID_는_전부_대장에_있다():
    for answer in _all_answers():
        brief = answer.brief
        used = [c.target for c in brief.cards] + [g.id for g in brief.gaps]
        used += [x for a in brief.attacks for x in (a.id, a.target, a.risk) + ((a.gap,) if a.gap else ())]
        assert all(ids.is_known(x) for x in used), used


def test_실데이터로도_guard_를_통과한다():
    """로컬에 파생 점수가 있을 때만 돈다. 🔒 아무것도 쓰지 않는다."""
    path = ROOT / "data" / "derived" / "score_daily.parquet"
    if not path.is_file():
        pytest.skip("로컬 파생 점수가 없다")
    from sector.sector_master import load

    master = load()
    names = view.Names.of(master)
    frame = pd.read_parquet(path)
    for sid in sorted(frame["sector_id"].unique()):
        jobs = [(name, "balanced", {}) for name in intent.INTENTS]
        jobs += [("changed", "balanced", {"window_days": w}) for w in (1, 20, 60)]
        jobs += [("trust", p, {}) for p in ("momentum", "contrarian")]
        for name, profile, kw in jobs:
            answer = engine.brief_for(name, frame=frame, sector_id=sid, names=names, master=master,
                                      profile=profile, **kw)
            assert answer.violations == (), (sid, name, profile, kw, answer.violations)


def _map(brief: compose.Brief, fn) -> compose.Brief:
    def fix(sentences):
        return tuple(fn(s) for s in sentences)

    cards = tuple(dataclasses.replace(
        c, observed=fix(c.observed), meaning=fix(c.meaning), cause=fix(c.cause),
        counter=fix(c.counter), limits=fix(c.limits), next_kpi=fix(c.next_kpi)) for c in brief.cards)
    return dataclasses.replace(brief, headline=fix(brief.headline), cards=cards, listing=fix(brief.listing))


def _swap(brief: compose.Brief, old: str, new: str) -> compose.Brief:
    """템플릿과 글자를 **함께** 바꾼다 — 조립 코드 자체가 틀린 경우를 흉내 낸다."""
    return _map(brief, lambda s: dataclasses.replace(
        s, template=s.template.replace(old, new), text=s.text.replace(old, new)) if old in s.template else s)


def _retemplate(brief: compose.Brief, sentence: compose.Sentence, template: str, **changes) -> compose.Brief:
    """문장 하나의 틀을 바꾸고 **장부 값으로 다시 채운다** — 글자는 틀과 맞으니 재채움 대조로는 안 잡힌다."""
    values = {k: e.value for k, e in brief.evidence.items()}
    new = dataclasses.replace(sentence, template=template, text=slots.fill(template, values),
                              cites=slots.cites_of(template) or sentence.cites, **changes)
    return _map(brief, lambda s: new if s is sentence else s)


def _verify(brief, **kw):
    return guard.verify(brief, frame=FRAME, master=MASTER, **kw)


def _has(violations, word):
    return any(word in v for v in violations)


def test_축_문장의_방향을_거꾸로_적으면_잡는다():
    """🔴 밸류 축 부호를 거꾸로 적을 뻔했던 일 — 장부 값은 맞고 **틀과 글자가 함께** 틀린 경우다."""
    caught = 0
    pairs = (("**아래**", "**위**"), ("**위**", "**아래**"), ("더 올랐다", "덜 올랐다"),
             ("덜 올랐다", "더 올랐다"), ("% 늘었다", "% 줄었다"), ("% 줄었다", "% 늘었다"),
             ("%p 많다", "%p 적다"), ("%p 적다", "%p 많다"))
    for sid in SECTORS:
        brief = _brief("why_rank", sid).brief
        for sentence in (s for c in brief.cards for s in c.observed if s.role == "axis_plain"):
            for old, new in pairs:
                if old in sentence.template:
                    assert _has(_verify(_swap(brief, old, new)), "방향 낱말"), (sid, old)
                    caught += 1
                    break
    assert caught >= 3, "합성 표가 방향 문장을 충분히 만들지 않았다"


def test_해석과_방향이_따로_놀면_잡는다():
    """🔴 "줄었다 — (설정을 늘렸다는 뜻)" 이 통과했었다(리뷰 O2). omega 는 자금흐름이 끌어내린다."""
    brief = _brief("why_rank", "omega").brief
    assert any("% 줄었다" in s.text for c in brief.cards for s in c.observed)
    broken = _swap(brief, "돈이 빠져나간 자국이다", "운용사가 설정을 늘렸다는 뜻이고, 실제로 돈이 들어온 자국이다")
    assert _has(_verify(broken), "방향 낱말")


def test_기여를_거꾸로_적으면_잡는다():
    brief = _brief("why_rank", "alpha").brief
    contrib = next(s for c in brief.cards for s in c.observed if s.role == "contrib")
    word = "보탰다" if "보탰다" in contrib.template else "깎았다"
    assert _has(_verify(_swap(brief, word, "깎았다" if word == "보탰다" else "보탰다")), "반대")


def test_총점_문장에_구간_낱말을_다시_붙이면_잡는다():
    """🔒 ADR-SC-0020 ① 을 **guard 가 강제한다.** 밴드를 되살린 문장은 원천으로 다시
    정한 틀과 다르므로 거부된다 — 결정이 글로만 남지 않는다.

    🔴 더 싸 보이는 길(«어디에도 못 쓰는 낱말» 목록 `_ALL_WORDS` 의 여분에 «높다·낮다·
       비슷하다» 를 넣기)은 **실측으로 기각했다** — `explain.AXIS_MEANING["B"]`
       ("고르게 오르면 높다. 한 종목만 급등하면 오히려 낮다")와 `ids` 의 `RK-HEAT`
       ("이미 많이 올라 밸류가 낮다")가 그 낱말을 **정당하게** 쓰고 있어서, 낱말로 막으면
       멀쩡한 문장이 함께 죽는다. 🔒 옛 밴드 검사(`_SIGMA_PATTERNS`)가 사라진 자리를
       이 검사가 메운다 — 지우고 비워 두지 않는다.
    """
    brief = _brief("why_rank", "alpha").brief
    sigma = brief.headline[1]
    revived = sigma.template + " '다른 섹터들보다 뚜렷이 높다' 는 뜻이다."
    assert _has(_verify(_retemplate(brief, sigma, revived)), "다시 정한 문장")


def test_순위와_섹터_수의_자리를_맞바꾸면_잡는다():
    """🔴 "11개 섹터 중 21위" — 두 숫자가 다 근거에 있어 통과했었다(리뷰 R2)."""
    brief = _brief("why_rank", "alpha").brief
    head = brief.headline[0]
    swapped = (head.template.replace("⟦EV-TOTAL|int⟧", "@@").replace("⟦EV-RANK|int⟧", "⟦EV-TOTAL|int⟧")
               .replace("@@", "⟦EV-RANK|int⟧"))
    violations = _verify(_retemplate(brief, head, swapped))
    assert _has(violations, "뒤의 단위") and _has(violations, "다시 정한 문장")


def test_같은_단위의_다른_근거로_바꿔치면_잡는다():
    """🔴 균형 순위 자리에 역발상 순위를 넣어도 단위가 같아 통과했었다(재검증 R-A)."""
    brief = _brief("why_rank", "alpha").brief
    head = brief.headline[0]
    wrong = head.template.replace("⟦EV-RANK|int⟧", "⟦EV-RANK-CONTRARIAN|int⟧")
    assert _has(_verify(_retemplate(brief, head, wrong)), "다시 정한 문장")


def test_두_기준일의_순위를_맞바꾸면_잡는다():
    brief = _brief("changed", "alpha").brief
    change = next(s for s in brief.headline if s.key == "rank_change")
    swapped = (change.template.replace("⟦EV-PAST-RANK|int⟧", "@@").replace("⟦EV-RANK|int⟧", "⟦EV-PAST-RANK|int⟧")
               .replace("@@", "⟦EV-RANK|int⟧"))
    assert _has(_verify(_retemplate(brief, change, swapped)), "다시 정한 문장")


def test_목록_밖_산문이나_방향_말을_끼우면_잡는다():
    """🔴 "지금 담기 좋은 섹터다" · "순위가 상승했다" 가 통과했었다(재검증 R-A)."""
    brief = _brief("why_rank", "alpha").brief
    head = brief.headline[0]
    for template in ("**⟦EV-LABEL|text⟧**는 지금 담기 좋은 섹터다.",
                     head.template + " 순위가 상승했다.",
                     head.template.replace("⟦EV-TOTAL|int⟧개", "스물한 개"),
                     head.template.replace("⟦EV-TOTAL|int⟧개", "２１개")):
        assert _has(_verify(_retemplate(brief, head, template)), "다시 정한 문장"), template


def test_결론_문장을_더하거나_빼면_잡는다():
    brief = _brief("why_rank", "alpha").brief
    values = {k: e.value for k, e in brief.evidence.items()}
    template = "**⟦EV-LABEL|text⟧** 의 이 순위는 믿어도 된다."
    extra = compose.Sentence("since", template, slots.fill(template, values), ("EV-LABEL",))
    assert _has(_verify(dataclasses.replace(brief, headline=brief.headline + (extra,))), "문장이 3개다")
    assert _has(_verify(dataclasses.replace(brief, headline=brief.headline[:1])), "문장이 1개다")


def test_목록이나_빈칸을_빼거나_닫으면_잡는다():
    contents = _brief("contents", "alpha").brief
    assert _has(_verify(dataclasses.replace(contents, listing=())), "목록")
    trust = _brief("trust", "omega").brief
    assert _has(_verify(dataclasses.replace(trust, gaps=())), "빈칸 목록")
    closed = tuple(g.with_state("닫힘") for g in trust.gaps)
    assert _has(_verify(dataclasses.replace(trust, gaps=closed)), "빈칸")


def test_장부에서_근거를_지우면_잡는다():
    """🔴 ETF 수를 지우고 "알려진 한계 없다" 로 바꾸면 기대가 함께 사라져 통과했었다(재검증 R-B)."""
    brief = _brief("why_rank", "beta").brief
    evidence = {k: e for k, e in brief.evidence.items() if k != "EV-ETF-N"}
    none = compose.Sentence("fixed", compose.LIMIT_NONE, compose.LIMIT_NONE)
    cards = tuple(dataclasses.replace(c, limits=(none,)) if c.axis == "F" else c for c in brief.cards)
    assert _has(_verify(dataclasses.replace(brief, evidence=evidence, cards=cards)), "있어야 할 근거 EV-ETF-N")
    trust = _brief("trust", "alpha").brief
    stripped = {k: e for k, e in trust.evidence.items() if not k.startswith(("EV-RANK", "EV-AT-"))}
    assert _has(_verify(dataclasses.replace(trust, evidence=stripped, attacks=())), "있어야 할 근거")


def test_σ_의_부호를_떼면_잡는다():
    brief = _brief("why_rank", "delta").brief
    sigma = brief.headline[1]
    assert _has(_verify(_retemplate(brief, sigma, sigma.template.replace("|sigma⟧", "|pct2⟧"))), "pct2")


def test_글자만_바꾸면_다시_채운_것과_달라서_잡는다():
    brief = _brief("why_rank", "alpha").brief
    head = brief.headline[0]
    broken = _map(brief, lambda s: dataclasses.replace(s, text=s.text.replace("위", "위 · 9", 1)) if s is head else s)
    assert _has(_verify(broken), "다시 채운 것과 다르다")


def test_자리_밖에_숫자를_적으면_잡는다():
    brief = _brief("why_rank", "alpha").brief
    head = brief.headline[0]
    assert _has(_verify(_retemplate(brief, head, head.template + " 기여 +9999bp · 3x")), "자리 밖에")


def test_장부_값을_바꾸면_원천과_대조해_잡는다():
    brief = _brief("why_rank", "alpha").brief
    evidence = dict(brief.evidence)
    evidence["EV-RANK"] = dataclasses.replace(evidence["EV-RANK"], value=evidence["EV-RANK"].value + 1)
    assert any(v.startswith("EV-RANK:") for v in _verify(dataclasses.replace(brief, evidence=evidence)))


def test_원천을_바꾸면_장부와_어긋난다():
    """🔒 guard 가 장부가 아니라 **표**를 본다는 증거 — 같은 브리프를 다른 표로 대조한다."""
    brief = _brief("why_rank", "alpha").brief
    mutated = FRAME.copy()
    mask = (mutated["bas_dd"] == DAYS[-1]) & (mutated["sector_id"] == "alpha")
    mutated.loc[mask, "v_raw_bp"] = -mutated.loc[mask, "v_raw_bp"]
    assert any(v.startswith("EV-V-RAW:") for v in guard.verify(brief, frame=mutated, master=MASTER))


def test_열쇠를_바꿔_역할을_내려도_잡는다():
    """🔴 역할 라벨만 믿어 검사를 건너뛰었다(리뷰 O1)."""
    brief = _brief("changed", "alpha").brief
    change = next(s for s in brief.headline if s.key == "rank_change")
    demoted = _map(brief, lambda s: dataclasses.replace(s, key="since") if s is change else s)
    violations = _verify(demoted)
    assert _has(violations, "검사하지 않는 문장") and _has(violations, "다시 정한 문장")


def test_역할_낱말이_한_문장에_두_번_나오면_잡는다():
    """🔴 "아래이고 … 많다" 가 통과했었다(재검증 R-G)."""
    brief = _brief("why_rank", "alpha").brief
    plain = next(s for c in brief.cards for s in c.observed if s.role == "axis_plain")
    word = next(w for w in ("**아래**", "**위**", "더 올랐다", "덜 올랐다", "늘었다", "줄었다", "많다", "적다")
                if w in plain.template)
    assert _has(_verify(_retemplate(brief, plain, plain.template + f" 게다가 {word}")), "번 나온다")


def test_카드_제목을_바꾸면_잡는다():
    brief = _brief("why_rank", "alpha").brief
    cards = tuple(dataclasses.replace(c, title=c.title + " — 순위가 올랐다") if c.kind == "stability" else c
                  for c in brief.cards)
    assert _has(_verify(dataclasses.replace(brief, cards=cards)), "제목")


def test_다른_축의_상수를_넣으면_잡는다():
    """🔴 B 카드 의미 칸에 M 축 문장을 넣어도 통과했었다(리뷰 O3)."""
    brief = _brief("why_rank", "alpha").brief
    card = brief.cards[0]
    other = next(a for a in AXES if a != card.axis)
    text = explain.AXIS_MEANING[other]
    wrong = dataclasses.replace(card, meaning=(compose.Sentence("fixed", text, text),))
    assert _has(_verify(dataclasses.replace(brief, cards=(wrong,) + brief.cards[1:])), "meaning")


def test_필요한_한계를_없다고_바꾸면_잡는다():
    """🔴 ETF 1종 섹터의 자금흐름 카드에 "알려진 한계는 없다" 가 통과했었다(리뷰 O3). beta 는 ETF 가 하나다."""
    brief = _brief("why_rank", "beta").brief
    card = next(c for c in brief.cards if c.axis == "F")
    assert compose.LIMIT_ONE_ETF in [s.text for s in card.limits]
    none = compose.Sentence("fixed", compose.LIMIT_NONE, compose.LIMIT_NONE)
    cards = tuple(dataclasses.replace(c, limits=(none,)) if c is card else c for c in brief.cards)
    assert _has(_verify(dataclasses.replace(brief, cards=cards)), "limits")


def test_짧은_이력_한계를_빠뜨리면_잡는다():
    brief = _brief("why_rank", "alpha", days=30).brief
    card = next(c for c in brief.cards if c.kind == "stability")
    assert compose.STAB_SHORT_LIMIT in [s.text for s in card.limits]
    cards = tuple(dataclasses.replace(c, limits=c.limits[:1]) if c is card else c for c in brief.cards)
    assert _has(_verify(dataclasses.replace(brief, cards=cards)), "limits")


def test_점수가_있는데_점수를_낼_수_없다고_하면_잡는다():
    brief = _brief("why_rank", "alpha").brief
    values = {k: e.value for k, e in brief.evidence.items()}
    template = templates.no_score("알파")
    fake = (compose.Sentence("no_score", template, slots.fill(template, values), ("EV-LABEL", "EV-RANK", "EV-SCORE")),
            compose.Sentence("fixed", compose.NO_SCORE_TEXT, compose.NO_SCORE_TEXT))
    assert _has(_verify(dataclasses.replace(brief, headline=fake)), "점수가 있는데")


def test_반론_판정을_뒤집으면_잡는다():
    """🔴 판정만 뒤집고 방어 문장 · 개수까지 맞춰도 통과했었다(리뷰 O4)."""
    brief = _brief("trust", "alpha").brief
    values = {k: e.value for k, e in brief.evidence.items()}
    target = next(a for a in brief.attacks if a.id == "AT-4")
    assert target.case == "pass"
    template = redteam.defense_template("AT-4", "weak")
    flipped = dataclasses.replace(target, case="weak", defense_template=template,
                                  defense=slots.fill(template, values))
    attacks = tuple(flipped if a is target else a for a in brief.attacks)
    assert _has(_verify(dataclasses.replace(brief, attacks=attacks)), "판정 갈래")


def test_쓰지_않는_표현을_잡는다():
    brief = _brief("why_rank", "alpha").brief
    head = brief.headline[0]
    for phrase in ("유망하다", "매력적이다", "압도적으로", "오를 것이다", "호재다", "사세요", "저평가다"):
        broken = _retemplate(brief, head, head.template + " " + phrase)
        assert _has(_verify(broken), "쓰지 않는 표현"), phrase


def test_해석_카드의_칸이_비면_잡는다():
    brief = _brief("why_rank", "alpha").brief
    broken = dataclasses.replace(brief, cards=(dataclasses.replace(brief.cards[0], counter=()),)
                                 + brief.cards[1:])
    assert _has(_verify(broken), "counter")


def test_면책이_바뀌면_잡는다():
    brief = _brief("why_rank", "alpha").brief
    changed = dataclasses.replace(brief, disclaimer=compose.Sentence("disclaimer", "요약이다.", "요약이다."))
    assert _has(_verify(changed), "면책")


def test_빈_값이_글자로_새면_잡는다():
    """🔴 "그중 ETF 는 None개다" 가 통과했었다(리뷰 O6)."""
    brief = _brief("contents", "alpha").brief
    card = brief.cards[0]
    leaked = dataclasses.replace(card.observed[0], text="그중 기준일 점수에 들어간 ETF 는 None개다.")
    cards = (dataclasses.replace(card, observed=(leaked,) + card.observed[1:]),)
    assert _has(_verify(dataclasses.replace(brief, cards=cards)), "빈 값")


# ── 5. 엔진의 행동 ──────────────────────────────────────────────────────────

def test_어긋난_빈_값이_섞여도_브리프가_선다():
    """🔴 ETF 수 · 쓴 축 수가 비고, 밸류가 비었는데 결측 표시는 빈 행 — 브리프 전체가 숨었었다(리뷰 O5 · O6)."""
    trust = _brief("trust", "zeta")
    assert trust.ok, trust.violations
    cases = {a.id: (a.status, a.gap) for a in trust.brief.attacks}
    assert cases["AT-5"] == ("추가확인", "GP-ETF-N")
    assert cases["AT-6"] == ("추가확인", "GP-AXIS-V")
    assert cases["AT-7"] == ("추가확인", "GP-AXES")
    gap_ids = {g.id for g in trust.brief.gaps}
    assert {"GP-ETF-N", "GP-AXIS-V", "GP-AXES"} <= gap_ids
    contents = _brief("contents", "zeta")
    assert contents.ok, contents.violations
    assert any("ETF 수는 알 수 없다" in s.text for c in contents.brief.cards for s in c.observed)


def test_질문에_적힌_섹터가_다르면_머리에_적는다():
    answer = _ask("감마 왜 이 순위야", context="alpha")
    assert answer.sector_id == "gamma" and answer.ok
    assert "알파" in answer.notice and "감마" in answer.notice and "질문에 적힌" in answer.notice


def test_후속_질문은_인계받은_섹터로_답하고_그렇다고_말한다():
    first = _ask("감마 왜 이 순위야", context="alpha")
    follow = _ask("그럼 믿어도 돼?", context="alpha", previous=first.handoff)
    assert follow.sector_id == "gamma" and follow.brief.intent == "trust"
    assert "앞 질문에서 이어진" in follow.notice                        # 🔴 "질문에 적힌" 이 아니다(리뷰 O8)
    moved = _ask("그럼 믿어도 돼?", context="delta", previous=first.handoff)
    assert moved.sector_id == "delta" and moved.notice is None


def test_섹터_이름을_못_읽으면_화면의_섹터로_답하지_않는다(real_master):
    """🔴 "KODEX반도체 왜 1위야" 에 화면에서 고른 섹터로 조용히 답했다(재검증 R-C)."""
    answer = engine.answer("KODEX반도체 왜 1위야", frame=FRAME, context_sector="alpha", names=view.Names.of(real_master),
                           master=real_master)
    assert answer.brief is None and answer.route.reason == "sector_unclear"
    assert "반도체" in answer.message


def test_거절하면_브리프를_만들지_않는다():
    answer = _ask("알파 사야 돼?")
    assert answer.brief is None and answer.message == intent.REFUSAL_TEXT["trade"]


def test_GICS_를_물으면_그_안의_섹터를_말한다():
    answer = _ask("정보기술 왜 높아")
    assert answer.route.reason == "gics" and "알파" in answer.message
    empty = _ask("에너지 왜 낮아")
    assert "섹터가 없다" in empty.message


def test_표에_없는_섹터는_짐작하지_않는다():
    answer = _brief("why_rank", "nope")
    assert answer.brief is None and answer.message == engine.NOT_IN_TABLE


def test_확정한_뒤는_확정한_날_전_마지막_기준일과_견준다():
    """🔴 08-20 16:30 UTC 는 KST 08-21 새벽이다 — 그 날 전의 마지막 기준일은 08-20 이다."""
    answer = _brief("changed", workspace=_workspace(), team_id="t1", since_confirm=True)
    assert answer.brief.past_as_of == "20260820" and answer.ok


def test_확정하지_않았으면_확정_이후를_지어내지_않는다():
    answer = _brief("changed", workspace=_workspace(confirmed_at=None), team_id="t1", since_confirm=True)
    assert answer.brief.past_as_of is None
    assert [g.id for g in answer.brief.gaps][0] == "GP-CONFIRM" and answer.ok


def test_창_밖이면_가까운_날로_바꿔_끼우지_않는다():
    answer = _brief("changed", window_days=60)
    assert answer.brief.past_as_of is None and "GP-PAST" in [g.id for g in answer.brief.gaps]


def test_그날_행이_없으면_가까운_날로_바꿔_끼우지_않는다():
    """omega 는 열째 날 행이 없다 — 그날로 견주라고 하면 빈칸이어야 한다."""
    back = len(DAYS) - 1 - 9
    answer = _brief("changed", "omega", window_days=back)
    assert answer.brief.past_as_of is None and "GP-PAST" in [g.id for g in answer.brief.gaps]


def test_창을_적지_않았을_때만_기본_창을_골랐다고_말한다():
    """🔒 알아서 고른 창은 고른 사실을 말한다 — 적은 창에 "적지 않았다" 고 하지 않는다(재검증 R-E)."""
    default = _ask("뭐가 바뀌었어")
    assert default.ok and any("기본값" in s.text for s in default.brief.headline)
    written = _ask("알파 두 주 전보다 뭐가 바뀌었어")
    assert written.ok and written.brief.window_days == 10
    assert not any("기본값" in s.text for s in written.brief.headline)


def test_순위_이력에_구멍이_있으면_안정성을_말하지_않는다():
    answer = _brief("trust", "omega")
    assert "GP-STAB" in [g.id for g in answer.brief.gaps]
    swing = next(a for a in answer.brief.attacks if a.id == "AT-2")
    assert swing.status == "추가확인" and swing.gap == "GP-STAB"


def test_같은_질문을_다시_하면_빈칸이_유지되고_구조적_빈칸은_인계에_따로_남는다():
    first = _ask("오메가 믿어도 돼?", context="omega")
    again = _ask("오메가 믿어도 돼?", context="omega", previous=first.handoff)
    states = {g.id: g.state for g in again.brief.gaps}
    assert states["GP-STAB"] == "유지"
    assert "GP-INVESTOR" in again.handoff.structural
    assert all(gap_id != "GP-INVESTOR" for gap_id, _, _ in again.handoff.unresolved)
    assert again.ok


def test_창이_다른_질문에서는_빈칸이_닫혔다고_하지_않는다():
    """🔴 15영업일 전 → 어제 로 창만 바꿨는데 GP-PAST 가 '닫힘' 으로 나왔다(리뷰 O8)."""
    first = _ask("오메가 15영업일 전보다 뭐가 바뀌었어", context="omega")
    assert "GP-PAST" in [g.id for g in first.brief.gaps]
    second = _ask("오메가 어제보다 뭐가 바뀌었어", context="omega", previous=first.handoff)
    assert all(g.state != "닫힘" for g in second.brief.gaps)


def test_못_막은_반론은_논점을_약화시키고_리스크를_유효로_만든다():
    answer = _brief("trust", "beta")                       # ETF 1종
    ledger_rows = {k: s for k, _, s in answer.handoff.ledger}
    assert ledger_rows["RK-ONE-ETF"] == "유효" and ledger_rows["TH-F"] == "약화"


def test_유동성_경고와_실측이_다르면_출처_충돌로_적는다():
    """alpha: yaml 경고 켜짐 · 실측 충분 / beta: yaml 경고 없음 · 실측 충분(충돌 없음)."""
    assert "GP-YAML-LIQ" in [g.id for g in _brief("contents", "alpha").brief.gaps]
    assert "GP-YAML-LIQ" not in [g.id for g in _brief("contents", "beta").brief.gaps]


def test_섹터_정의가_없으면_구성을_말하지_않는다():
    answer = engine.brief_for("contents", frame=FRAME, sector_id="alpha", names=view.Names.empty())
    assert answer.ok and answer.brief.listing == ()
    assert "GP-MASTER" in [g.id for g in answer.brief.gaps]


def test_gaps_for_는_순서가_고정이다():
    inventory = collect(FRAME, "omega", profile="balanced", days=20, names=NAMES, master=MASTER)
    assert [g.id for g in gaps_for(inventory, "trust")] == [g.id for g in gaps_for(inventory, "trust")]


# ── 6. 골든 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sector_id", ["alpha", "omega"])
@pytest.mark.parametrize("intent_name", intent.INTENTS)
def test_브리프_골든(snapshot, intent_name, sector_id):
    """🔒 문구 · 근거 · 판정의 모양을 고정한다. 바뀐 이유를 설명할 수 있을 때만 갱신한다."""
    snapshot.assert_match(_brief(intent_name, sector_id).brief.to_dict())


def test_확정_이후_골든(snapshot):
    snapshot.assert_match(_brief("changed", workspace=_workspace(), team_id="t1",
                                 since_confirm=True).brief.to_dict())


def test_빈_값_섹터_골든(snapshot):
    snapshot.assert_match({name: _brief(name, "zeta").brief.to_dict() for name in ("trust", "contents")})


# ── 7. 정적 검사 ────────────────────────────────────────────────────────────

def _agent_sources():
    return [p for p in (ROOT / "dashboard" / "agent").glob("*.py") if not p.name.endswith("_test.py")]


def test_에이전트는_읽기만_한다():
    """🔒 원장에 쓰지 않고 네트워크를 부르지 않고 벽시계를 읽지 않고 화면을 모른다."""
    forbidden = re.compile(
        r"import streamlit|from streamlit|import requests|huggingface_hub|workspace\.store|"
        r"workspace import .*events|\.append\(\[|datetime\.now|date\.today|utcnow|astimezone\(\)|"
        r"time\.time\(|import random|anthropic|openai|ollama|transformers")
    for path in _agent_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not forbidden.search(line), f"{path.name}:{number} — {line.strip()}"


def test_점수_경로는_에이전트를_모른다():
    """🔒 절대 제약 2 · 3 — 에이전트 출력이 점수 · 순위 · 게시로 되먹이지 않는다."""
    for folder in ("sector", "batch"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert not re.search(r"dashboard\.agent|from dashboard|import dashboard", text), path


def _squash(text: str) -> str:
    """한글 · 영숫자만 남긴다 — 구분선(`───`) · 표 기호가 겹치는 것은 문장 복사가 아니다."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text)


_GIC_WINDOW = 12
#: 일반 용어 — 장치 이름 · 판정 낱말 · 폰트 이름. 문장이 아니다
_GIC_NAMES = ("관찰사실", "가능한원인가설", "반대해석", "다음확인", "추가확인", "구조적비공시", "구조적미집계",
              "출처충돌", "미확보", "GapLog", "RedTeam", "자료확보로그", "해석카드여섯칸", "고정ID대장",
              "통과취약추가확인", "ProceedWatchDrop", "schemaversion", "JetBrainsMono", "Pretendard",
              "손익계산서재무상태표현금흐름표",
              # 원문 문서 제목 — 어느 문서를 입력으로 받았는지 적는 이름이다
              "산업리서치", "기업리서치", "기업TopPick", "산업TopPick")
_GIC_SKIP = ("backend", ".venv", "data", "node_modules", "private", "testdata", "__pycache__")


def test_GIC_원문_문장을_옮기지_않았다():
    """🔴 원문은 가천대 GIC 저작물이고 저장소는 Public 이다 — **구조만** 옮긴다(리뷰 R4 · 재검증).

    저장소의 `.py` · `.md` 전부에서 원문과 **12글자 연속 일치**를 찾는다(따옴표로 인용한 짧은 구절까지 잡으려고
    15에서 낮췄다). 원문은 gitignore 라 로컬에만 있다 — 없으면 이 검사는 건너뛴다.
    """
    sources = sorted((ROOT / "docs" / "private" / "gic").glob("*.md"))
    if not sources:
        pytest.skip("GIC 원문이 로컬에 없다")
    windows: set[str] = set()
    for path in sources:
        text = _squash(path.read_text(encoding="utf-8"))
        windows.update(text[i:i + _GIC_WINDOW] for i in range(len(text) - _GIC_WINDOW + 1))
    # 🔒 점으로 시작하는 폴더(`.claude` · `.claude-flow` 등)는 도구 설정이지 이 저장소의 글이 아니다
    targets = [p for pattern in ("*.py", "*.md") for p in ROOT.rglob(pattern)
               if not any(part in _GIC_SKIP or part.startswith(".") for part in p.relative_to(ROOT).parts)]
    hangul = re.compile(r"[가-힣]")
    hits = []
    for path in targets:
        text = _squash(path.read_text(encoding="utf-8", errors="ignore"))
        for name in _GIC_NAMES:
            text = text.replace(name, "¦")
        # 🔒 한글이 없는 창은 세지 않는다 — "distribution" 같은 영어 낱말이 겹치는 것은 문장 복사가 아니다
        hits += [(str(path.relative_to(ROOT)), text[i:i + _GIC_WINDOW])
                 for i in range(len(text) - _GIC_WINDOW + 1)
                 if "¦" not in text[i:i + _GIC_WINDOW] and hangul.search(text[i:i + _GIC_WINDOW])
                 and text[i:i + _GIC_WINDOW] in windows]
    assert not hits, hits[:10]


# ── 8. 화면 ────────────────────────────────────────────────────────────────

def _markdown(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def _keys(elements) -> list[str]:
    return [element.key for element in elements]


def test_랭킹에_질문칸이_있고_기본은_왜_이_자리인가다(ledger):
    at = _open_as(_ranking_page, ledger)
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert "rank_ask" in _keys(at.text_input)
    assert any("왜 이 자리인가" in c.value for c in at.caption)
    assert "앞으로 오른다는 뜻이 아니" in _markdown(at)


def test_거절할_질문이면_브리프_대신_거절문이_나온다(ledger):
    at = _open_as(_ranking_page, ledger)
    at.text_input(key="rank_ask").input("이거 사야 돼?").run()
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert any(intent.REFUSAL_TEXT["trade"] in i.value for i in at.info)
    assert not any("질문 —" in c.value for c in at.caption)


def test_질문_버튼이_칸을_채우고_반론표가_나온다(ledger):
    at = _open_as(_ranking_page, ledger)
    at.button(key="rank_ask__trust").click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert at.text_input(key="rank_ask").value == intent.EXAMPLE_QUESTIONS["trust"]
    assert any("믿어도 되나" in c.value for c in at.caption)
    assert len(at.dataframe) >= 1


def test_섹터를_바꾸면_질문칸이_빈다(ledger):
    """🔴 옛 질문이 새 섹터 패널 아래에서 옛 섹터로 답했다(리뷰 O8)."""
    at = _open_as(_ranking_page, ledger)
    at.text_input(key="rank_ask").input("이 순위 믿어도 돼?").run()
    assert at.text_input(key="rank_ask").value
    at.selectbox(key="rank_detail").select_index(1).run()
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert at.text_input(key="rank_ask").value == ""


def test_같은_질문을_다시_그려도_견줄_인계가_바뀌지_않는다(ledger):
    """🔴 다시 그릴 때마다 자기 인계와 견줘 빈칸이 "신규 → 유지" 로 바뀌었다(재검증 R-F)."""
    at = _open_as(_ranking_page, ledger)
    at.text_input(key="rank_ask").input("이 순위 믿어도 돼?").run()
    first = at.session_state["rank_ask__handoff"]
    at.run()
    again = at.session_state["rank_ask__handoff"]
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert first["before"] is None and again["before"] is None
    assert again["question"] == "이 순위 믿어도 돼?"


def test_guard_가_어긋나면_문장을_버리고_이유를_말한다(ledger, monkeypatch):
    from dashboard import evidence

    monkeypatch.setattr(guard, "verify", lambda *a, **k: ("합성 위반 — 테스트",))
    at = _open_as(_ranking_page, ledger)
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert any(evidence.GUARD_FAILED in e.value for e in at.error)
    assert not any("질문 —" in c.value for c in at.caption)
    assert "섹터 중" not in _markdown(at)


def test_확정_모달에는_질문칸이_없다(ledger):
    at = _as_member(_ranking_page, ledger)
    at.button(key="open_confirm").click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert [k for k in _keys(at.text_input) if k and k.endswith("_ask")] == ["rank_ask"]


def test_조_페이지에도_질문칸이_있다(ledger):
    at = _as_member(_ranking_page, ledger)
    _confirm(at, "합성 사유")
    at = _open_as(_teams_page, ledger)
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert "team_ask" in _keys(at.text_input)
