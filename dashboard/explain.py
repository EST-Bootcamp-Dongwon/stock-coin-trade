"""화면 문구 — **템플릿뿐이다.**

## 🔴 왜 LLM 이 아닌가

절대 제약 2 — 매매 신호 생성 경로에 LLM 을 두지 않는다. 이유가 셋인데 그중
**사전학습 룩어헤드 오염**이 가장 무겁다. 모델은 2025~2026 한국 섹터에 무슨 일이
있었는지 이미 알고 있어서, "이 섹터가 유망하다" 는 문장이 우리 데이터가 아니라
**기억한 결과**에서 나올 수 있다. `test_asof_monotone` 한 줄로 구조적으로 막아 온
오염이 문장 생성 경로로 되돌아오는 셈이다.

그래서 여기 있는 것은 **f-string 에 숫자를 끼우는 일**뿐이고, 골든 테스트가 문구를
고정한다. 화면 문구가 언제 바뀌었는지 diff 로 보여야 하기 때문이다.

## 🔒 "무엇을 뜻하지 않는가" 를 함께 적는다

팀원 7명은 개발자가 아니다. 이 도구의 가장 큰 위험은 오독이고("1위 = 사면 오른다"),
축의 뜻만 적으면 그 위험이 줄지 않는다. 그래서 축마다 `NOT` 을 같이 둔다.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sector.scoring import AXIS_NAMES, PRESETS, lead_axis, trail_axis

if TYPE_CHECKING:                      # 🔒 순환 임포트를 만들지 않는다 — `view` 는
    from dashboard.view import Arithmetic   # `explain` 을 함수 안에서 늦게 임포트한다

__all__ = [
    "weighting_label",
    "AXIS_MEANING", "AXIS_NOT", "AXIS_UNIT",
    "axis_raw_text", "axis_line", "score_text", "rank_stability_text",
    "josa", "axis_plain", "narrative",
    "liquidity_text", "degraded_text", "lead_axis_text", "rank_badge",
    "middle_text", "arithmetic_text",
    "WATERFALL_ABSENT", "WATERFALL_READING",
]

#: 🔒 그림을 **조용히 빼지 않는다** (ADR-SC-0016 ④). 왜 없는지 말하고, 무엇이
#:    어긋났는지는 바로 아래 `arithmetic_text` 가 경우마다 다르게 말한다 —
#:    총점이 없다 / 들어간 축이 없다 / 한도를 넘었다. 여기서 그 셋을 다시 나누면
#:    같은 판정이 두 곳에서 갈릴 자리가 생긴다.
WATERFALL_ABSENT = (
    "그림은 **④ 열의 합과 총점을 맞춰 볼 수 있을 때만** 그린다 — 지금은 그렇지 않아 "
    "그리지 않는다. 바로 아래 문장이 무엇이 어긋났는지 말한다.")

#: 🔒 그림의 읽는 법. **부호는 위치가 말한다** — 색이 아니다 (이슈 #16 §5).
#:    🔴 마지막 줄이 이슈 #15 다: 점수에 안 들어간 축은 0 높이 막대가 아니라 **없다**.
WATERFALL_READING = (
    "0 에서 시작해 축마다 기여만큼 오르내리고, 맨 오른쪽이 <b>게시된 총점</b>이다 — "
    "쌓아 올린 합이 아니라 게시된 값 그대로다. 막대의 <b>위치가 부호를 말한다</b>; "
    "색에는 뜻이 없다. 점수에 들어가지 않은 축은 막대가 <b>아예 없다</b> — "
    "0 을 보탠 것이 아니라 들어가지 않은 것이다.")

#: 축이 **묻는 것**. 한 줄로 끝낸다 — 길면 안 읽힌다.
AXIS_MEANING: dict[str, str] = {
    "M": "시장(동일가중)보다 더 올랐는가 — 20일 6 : 60일 4 로 섞는다",
    "F": "ETF 상장좌수가 20영업일 새 늘었는가 — 실제로 돈이 들어왔다는 뜻이다",
    "B": "섹터 안에서 몇 종목이 같이 오르는가 — 시장 전체 비율을 뺀 값이다",
    "V": "120일 평균보다 덜 올랐는가 — 많이 오른 섹터가 감점된다",
}

#: 🔴 축이 **뜻하지 않는 것**. 오독을 막는 자리다.
AXIS_NOT: dict[str, str] = {
    "M": "앞으로 더 오른다는 뜻이 아니다. 이미 오른 만큼을 잰 것이다",
    "F": "좋은 종목이라는 뜻이 아니다. 운용사가 설정을 늘렸다는 사실이다",
    "B": "고르게 오르면 높다. 한 종목만 급등하면 오히려 낮다",
    "V": "싸다는 뜻이지 오른다는 뜻이 아니다. M 축을 견제하려고 둔 축이다",
}

#: 원시값의 단위. 축마다 뜻이 달라 한 가지로 못 적는다.
AXIS_UNIT: dict[str, str] = {
    "M": "%p (시장 대비 로그수익률)",
    "F": "% (상장좌수 변화율)",
    "B": "%p (시장 대비 상승종목 비율)",
    "V": "% (120일 평균 대비 · 부호 반전)",
}

_MISSING = "—"


def _pct(bp: int | None, *, digits: int = 2) -> str:
    """bp 정수를 퍼센트 문자열로. 🔒 `None` 은 `—` 다 — 0 으로 그리지 않는다."""
    if bp is None:
        return _MISSING
    return f"{bp / 100:+.{digits}f}"


def axis_raw_text(axis: str, raw_bp: int | None) -> str:
    """원시값을 그 축의 단위로 읽는다."""
    if raw_bp is None:
        return _MISSING
    if axis == "M":
        return f"{_pct(raw_bp)}%p"
    if axis == "F":
        return f"{_pct(raw_bp)}%"
    if axis == "B":
        return f"{_pct(raw_bp, digits=1)}%p"
    if axis == "V":
        return f"{_pct(raw_bp)}%"
    return _MISSING


def axis_line(axis: str, *, raw_bp: int | None, z_bp: int | None,
              rank: int | None, total: int) -> str:
    """축 한 줄 — 원시값 · 뜻 · 횡단면 순위."""
    name = AXIS_NAMES.get(axis, axis)
    if z_bp is None:
        return f"{name}: {_MISSING} (이 축은 계산되지 않았다 — 가중치를 다시 나눴다)"
    sigma = z_bp / 10000
    place = f"{total}개 중 {rank}위" if rank else "순위 없음"
    return f"{name}: {axis_raw_text(axis, raw_bp)} · {sigma:+.2f}σ · {place}"


def score_text(score_bp: int | None, rank: int | None, total: int) -> str:
    """총점 한 줄. 🔒 척도를 함께 적는다 — 숫자만 보면 100점 만점으로 읽는다."""
    if score_bp is None:
        return f"{_MISSING} (점수를 낼 수 없다)"
    return (f"{score_bp / 10000:+.2f}σ · {total}개 중 {rank}위"
            if rank else f"{score_bp / 10000:+.2f}σ")


def rank_stability_text(mean_rank: float | None, spread: float | None, days: int) -> str:
    """🔴 "오늘만 1등" 과 "계속 1등" 을 가른다.

    신고서는 나흘 뒤지만 운용은 3개월이다. 하루치 순위로 고르면 그 차이를 놓친다.
    """
    if mean_rank is None or spread is None:
        return f"최근 {days}영업일 순위: {_MISSING} (이력이 모자라다)"
    steadiness = "꾸준하다" if spread <= 2 else ("흔들린다" if spread <= 5 else "많이 흔들린다")
    return f"최근 {days}영업일 평균 {mean_rank:.1f}위 · 진폭 ±{spread:.1f} — {steadiness}"


def liquidity_text(ok: bool | None) -> str:
    """🔒 "아직 모른다" 와 "미달이다" 는 다른 말이다."""
    if ok is None:
        return "유동성: — (20영업일이 아직 안 찼다)"
    if ok:
        return "유동성: 충분 (일평균 거래대금 1억 이상)"
    return "🔴 유동성 미달 — ETF 로는 실제 매수가 어렵다. 구성종목을 본다"


def degraded_text(missing: str | None, degraded: str | None) -> str:
    """결측·강등 축을 사람 말로. 없으면 빈 문자열이라 화면이 조용하다."""
    parts = []
    if missing:
        names = ", ".join(AXIS_NAMES.get(a, a) for a in str(missing).split("|") if a)
        if names:
            parts.append(f"계산되지 않은 축: {names} (가중치를 다시 나눴다)")
    if degraded:
        names = ", ".join(AXIS_NAMES.get(a, a) for a in str(degraded).split("|") if a)
        if names:
            parts.append(f"척도를 한 단 내린 축: {names} (섹터들이 거의 같은 값이었다)")
    return " · ".join(parts)


def preset_label(profile: str) -> str:
    """프리셋 이름 + 가중치. 🔒 가중치를 숨기지 않는다 — 순위가 왜 다른지의 답이다."""
    weights = PRESETS.get(profile, {})
    body = " / ".join(f"{AXIS_NAMES[a]} {weights[a]}" for a in ("M", "F", "B", "V") if a in weights)
    korean = {"balanced": "균형", "momentum": "모멘텀 중시", "contrarian": "역발상"}
    return f"{korean.get(profile, profile)} — {body}"


def weighting_label(weighting: Any) -> str:
    """지금 쓰는 가중치를 한 줄로. 프리셋이면 이름, 아니면 **비율을 그대로** 적는다.

    🔒 커스텀에 이름을 지어 주지 않는다 — "공격형" 같은 말을 붙이면 화면이 그 가중치를
       추천하는 것처럼 읽힌다. 슬라이더는 민감도를 보는 도구이지 관점이 아니다.
    """
    if weighting.is_preset:
        return preset_label(weighting.name)
    body = " / ".join(f"{AXIS_NAMES[a]} {weighting.weights[a]}"
                      for a in ("M", "F", "B", "V") if a in weighting.weights)
    return f"직접 고른 가중치 — {body}"


# ── 서술 — 숫자를 **말로** 바꾼다 ───────────────────────────────────────────
# 🔴 팀 7명 중 개발자는 한 명이다. 숫자와 σ 만 늘어놓으면 "1위니까 사자" 로 읽힌다.
#    그래서 같은 사실을 문장으로 한 번 더 말한다. 🔒 여전히 **템플릿**이다 —
#    f-string 에 숫자를 끼우는 것이지 LLM 이 아니다 (머리주석).

def josa(word: str, pair: str) -> str:
    """`word` 뒤에 붙을 조사를 고른다. `pair` 는 `"은는"` · `"이가"` · `"을를"` 꼴.

    🔴 "밸류은(는)" 처럼 쓰면 화면이 기계가 쓴 티가 난다. 한글은 **받침 유무**로
       조사가 갈리고, 유니코드에서 `(코드 − 0xAC00) % 28` 이 0 이 아니면 받침이
       있다. 한글이 아닌 글자로 끝나면 받침 없는 쪽을 고른다 — 틀려도 읽히는
       쪽이기 때문이다.
    """
    with_final, without_final = pair[0], pair[1]
    if not word:
        return without_final
    # `철강 (steel)` 처럼 괄호가 붙으면 **괄호를 통째로 떼고** 앞의 한국어로 고른다.
    # 안 그러면 끝 글자가 `l` 이라 한글이 아닌 쪽으로 빠진다.
    stem = re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", word.strip()).rstrip("*_`  ")
    last = stem[-1:] or ""
    if not last or not ("\uac00" <= last <= "\ud7a3"):
        return without_final
    return with_final if (ord(last) - 0xAC00) % 28 else without_final


def _ida(word: str) -> str:
    """`밸류다` · `자금흐름이다`."""
    return "이다" if josa(word, "은는") == "은" else "다"


#: 🔴 **총점에는 구간 낱말을 쓰지 않는다** (2026-09-20 · 이슈 #17 ① · ADR-SC-0020).
#:    `0.4 / 1.0 / 2.0` 은 **축 z 단위의 문턱**이고, 총점은 그 z 들의 가중평균이라
#:    같은 단위가 아니다 — 축들이 완전 상관이 아니면 평균은 덜 퍼진다.
#:    실측(2026-09-20 · 291영업일): 축 0.967~1.274σ vs 총점 balanced 0.654 ·
#:    momentum 0.779 · contrarian 0.538. 그래서 20260918 contrarian 1위가
#:    «다소 높다»(실제 2.30 표준편차)로 읽혔다. 순위는 정확하니 순위로 말한다.

def axis_plain(axis: str, raw_bp: int | None) -> str:
    """원시값이 **실제로 무슨 일인지** 한 문장으로.

    🔴 밸류 축의 부호를 조심한다 — 정의가 `−(지수/120일평균 − 1)` 이라
       **양수가 "평균보다 아래 = 덜 올랐다"** 는 뜻이다. 반대로 적으면 화면이
       정확히 거꾸로 설명하게 된다.
    """
    if raw_bp is None:
        return "값이 없어 말할 수 없다"
    value = raw_bp / 100
    if axis == "M":
        side = "더 올랐다" if raw_bp > 0 else "덜 올랐다"
        return f"최근 20·60일 수익률이 시장 평균보다 {abs(value):.2f}%p {side}"
    if axis == "F":
        if raw_bp > 0:
            return (f"ETF 상장좌수가 20영업일 새 {value:.2f}% 늘었다"
                    f"(운용사가 설정을 늘렸다는 뜻이고, 실제로 돈이 들어온 자국이다)")
        return (f"ETF 상장좌수가 20영업일 새 {abs(value):.2f}% 줄었다"
                f"(돈이 빠져나간 자국이다)")
    if axis == "B":
        side = "많다" if raw_bp > 0 else "적다"
        return (f"섹터 안에서 오름세인 종목 비율이 시장 전체보다 "
                f"{abs(value):.1f}%p {side}")
    if axis == "V":
        if raw_bp > 0:
            return (f"지수가 120일 평균보다 {value:.2f}% **아래**에 있다"
                    f"(최근 많이 오르지 않았다는 뜻이다)")
        return (f"지수가 120일 평균보다 {abs(value):.2f}% **위**에 있다"
                f"(최근 많이 올라 과열 쪽이라는 뜻이다)")
    return "—"


def narrative(
    *,
    label: str,
    rank: int | None,
    total: int,
    score_bp: int | None,
    parts: list[dict],
    mean_rank: float | None = None,
    spread: float | None = None,
    window: int = 20,
    liquidity_ok: bool | None = None,
    etf_n: int | None = None,
    missing: str | None = None,
    degraded: str | None = None,
    arithmetic: "Arithmetic | None" = None,
) -> list[str]:
    """이 섹터가 왜 이 자리인지를 **문단으로** 설명한다. 문장 목록을 돌려준다.

    🔒 `arithmetic`(`view.Arithmetic`)이 «총점이 축들로 설명되지 않는다» 고 하면
       ②③(무엇이 밀어올렸나 · 깎았나)을 **닫는다.** 그 두 문장은 기여를 **총점에
       빗대어** 말하는데("이 축 하나가 총점에 +N 만큼 보탰다"), 총점이 기여의 합으로
       설명되지 않는 상태에서는 근거가 없다. 열어 두면 같은 화면이 스스로 모순된다
       (이슈 #13 적대적 설계 리뷰).
    """
    lines: list[str] = []

    # ① 어디에 있나
    if score_bp is None or rank is None:
        lines.append(
            f"**{label}**{josa(label, '은는')} 점수를 낼 수 없었다. "
            f"아래 표의 빈 칸이 이유다.")
        return lines
    lines.append(
        f"**{label}**{josa(label, '은는')} {total}개 섹터 중 **{rank}위**다. "
        f"점수는 {score_bp / 10000:+.2f}σ 다."
    )

    scored = [p for p in parts if p["contribution_bp"] is not None]
    # 🔒 `getattr(..., True)` 로 받지 않는다 — `Arithmetic` 이 아닌 것이 오면 ②③ 이
    #    **조용히 열린다.** 없는 속성은 시끄럽게 터지는 편이 낫다 (ADR-SC-0016)
    explained = arithmetic is None or arithmetic.explained
    if scored and not explained:
        lines.append(
            "축별 기여가 총점을 설명하지 못한다 — 어느 축이 끌어올렸는지 여기서는 "
            "말하지 않는다. 파생본을 다시 만들어야 한다."
        )
    if scored and explained:
        # ② 무엇이 밀어올렸나
        # 🔒 동점 규칙은 `sector.scoring.lead_axis` 하나다 — 여기서 다시 적지 않는다
        by_axis = {p["axis"]: p for p in scored}
        contributions = {axis: part["contribution_bp"] for axis, part in by_axis.items()}
        best = by_axis[lead_axis(contributions)]
        if best["contribution_bp"] > 0:
            name = AXIS_NAMES[best["axis"]]
            lines.append(
                f"이 자리를 만든 것은 주로 **{name}**{_ida(name)} — "
                f"이 축에서 {total}개 중 **{best['rank']}위**다. "
                f"{axis_plain(best['axis'], best['raw_bp'])}. "
                f"이 축 하나가 총점에 **{best['contribution_bp']:+d}** 만큼 보탰다."
            )
        # ③ 무엇이 깎았나
        worst = by_axis[trail_axis(contributions)]
        if worst["contribution_bp"] < 0 and worst["axis"] != best["axis"]:
            name = AXIS_NAMES[worst["axis"]]
            lines.append(
                f"반대로 **{name}**{josa(name, '은는')} "
                f"**{worst['contribution_bp']:+d}** 만큼 깎았다. "
                f"{axis_plain(worst['axis'], worst['raw_bp'])}."
            )

    # ④ 오늘만 반짝인 것인가
    if mean_rank is not None and spread is not None:
        steady = ("꾸준히 이 근처에 있었다" if spread <= 2
                  else "순위가 오르내렸다" if spread <= 5 else "순위가 많이 흔들렸다")
        lines.append(
            f"최근 {window}영업일 평균 순위는 **{mean_rank:.1f}위**이고 "
            f"진폭은 ±{spread:.1f} 다 — {steady}."
        )
    else:
        lines.append(
            f"최근 {window}영업일 이력이 모자라 '오늘만 반짝인 것인지' 는 말할 수 없다."
        )

    # ⑤ 믿지 말아야 할 이유
    cautions = []
    if liquidity_ok is False:
        cautions.append(
            "ETF 거래대금이 하루 평균 1억에 못 미친다 — **ETF 로는 실제 매수가 어렵다.** "
            "담으려면 구성종목을 봐야 한다")
    if etf_n == 1:
        cautions.append(
            "이 섹터의 ETF 가 **하나뿐**이라 자금흐름 점수가 그 한 종목에 통째로 달려 있다")
    tail = degraded_text(missing, degraded)
    if tail:
        cautions.append(tail)
    if cautions:
        lines.append("⚠️ **주의** — " + " 그리고 ".join(cautions) + ".")

    # ⑥ 🔴 마지막은 언제나 이 문장이다
    lines.append(
        "이것은 **지나간 데이터를 정해진 규칙으로 요약한 것**이다. "
        "앞으로 오른다는 뜻이 아니고, 무엇을 사라는 뜻도 아니다."
    )
    return lines


def lead_axis_text(axis: str | None) -> str:
    """등수 카드의 한 줄 — **무엇이 이 섹터를 끌어올렸나.**

    🔴 등수만 크게 그리면 "1위 = 사면 오른다" 로 읽힌다. 그래서 카드마다 이유를
       한 축으로 붙인다. 🔒 끌어올린 축이 없으면(전부 감점) 지어내지 않는다.
    """
    if axis is None:
        return "끌어올린 축이 없다 — 다른 섹터가 더 많이 깎였을 뿐이다"
    name = AXIS_NAMES.get(axis, axis)
    return f"**{name}**{josa(name, '이가')} 끌어올렸다"


def rank_badge(rank: int | None) -> str:
    """`1위` — 🔒 메달 이모지를 쓰지 않는다. 등수는 상장이 아니라 좌표다."""
    return f"{rank}위" if rank else "—"


def arithmetic_text(arithmetic: "Arithmetic") -> str:
    """④ 열의 세로 합과 총점을 **같은 단위로 나란히** 적는다 (`view.Arithmetic`).

    🔴 옛 문장은 `**합계 +12522 bp = 총점 +1.25σ**` 였다. 두 가지가 한꺼번에 거짓이다 —
       ① 왼쪽은 bp, 오른쪽은 σ 라 **팀원이 눈으로 검산할 수 있는 등식이 아니었다**
       ② 등호 자체가 16,758 (행×프리셋) 중 **5,276건(31.5%)** 에서 거짓이었다 (이슈 #13).

    🔒 **경우를 가르지 않고 언제나 차이와 한도를 적는다.** 읽는 법 화면은 그날 1위
       하나만 예시로 쓰는데 그 섹터가 마침 어긋나지 않는 날이 있다(최신일 `steel` 은
       잔차 0 이었다). 그때 등호만 보여주면 팀원은 「세로로 더하면 총점」이라는
       **규칙**을 배우고, 그 규칙은 같은 날 같은 화면의 다른 6개 섹터에서 거짓이다.
       ⚠️ 1위 섹터 자체도 균형 **74/266일**은 어긋난다 — 나흘 중 하루꼴이다.

    🔒 **원인을 단정하지 않는다.** 가운데 문장은 «이 잔차가 반올림 탓이다» 가 아니라
       «이 방법은 최대 ±N bp 까지 어긋난다» 는 **방법의 성질**이다 (ADR-SC-0007).
    """
    total = arithmetic.total_bp          # 🔒 `getattr` 기본값을 두지 않는다 (위와 같은 이유)
    if total is None:
        return "총점이 없어 ④ 열의 합을 맞춰 볼 수 없다."

    sigma = f"**{total / 10000:+.2f}σ**"
    parts_sum = arithmetic.parts_sum_bp
    if parts_sum is None:
        # 🔒 점수에 들어간 축이 없는데 총점이 있다 — 「반올림」으로 설명할 수 없다
        return (f"점수에 들어간 축이 하나도 없는데 총점 **{total:+d} bp** ({sigma}) 가 "
                f"있다 — 게시된 총점이 게시된 σ 로 재현되지 않는다. "
                f"파생본을 다시 만들어야 한다.")

    head = f"합계 **{parts_sum:+d} bp** · 총점 **{total:+d} bp** ({sigma})"
    residual, bound = arithmetic.residual_bp, arithmetic.bound_bp
    if not arithmetic.explained:
        return (f"{head} — 차이 **{residual:+d} bp** 는 반올림으로 설명되는 "
                f"한도(±{bound} bp)를 넘는다. 게시된 총점이 게시된 σ 로 재현되지 않는다 "
                f"— 파생본을 다시 만들어야 한다.")
    gap = "차이 **없다**" if residual == 0 else f"차이 **{residual:+d} bp**"
    why = ("축이 하나뿐이라 반올림으로 어긋날 자리가 없다."
           if bound == 0
           else f"축마다 bp 로 반올림하므로 최대 **±{bound} bp** 까지 어긋난다.")
    return f"{head} — {gap}. {why}"


def middle_text(graded_n: int) -> str:
    """«가운데» 가 몇 위인가 — 🔒 **데이터가 정하는 수다. 문장에 박지 않는다.**

    🔴 「가운데(11위)」라고 적고 싶어진다. 지금 파생본 266영업일이 **전부 21개**를
       채점해서 그 말이 참이기 때문이다. 그러나 21 은 상수가 아니다 —

    - 슬라이더로 **성긴 축 하나만** 남기면 그 축이 결측인 섹터는 점수를 못 낸다.
      실측: `F` 단독 가중치는 **101일이 20개**다. 짝수이면 중앙값은 10위와 11위의
      평균이라 **어느 섹터의 점수도 아니다** — 「11위」가 그 자리에서 거짓이 된다.
    - `sectors.yaml` 에 섹터를 더하면 그날부터 22개다.

    🔒 `ranking.py` 의 `_window()`(「요청한 20 을 그대로 쓰지 않는다」)와 같은 규율이다.
       이슈 #14 가 고치려는 결함이 바로 «화면이 산수에 대해 거짓을 말한다» 이므로,
       그것을 고치면서 같은 종류의 거짓을 새 자리에 심지 않는다.
    """
    if graded_n <= 0:
        return "가운데"
    half, odd = divmod(graded_n, 2)
    return f"가운데({half + 1}위)" if odd else f"가운데({half}·{half + 1}위 사이)"
