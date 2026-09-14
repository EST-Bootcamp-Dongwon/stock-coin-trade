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

from sector.scoring import AXIS_NAMES, PRESETS

__all__ = [
    "AXIS_MEANING", "AXIS_NOT", "AXIS_UNIT",
    "axis_raw_text", "axis_line", "score_text", "rank_stability_text",
    "josa", "sigma_words", "axis_plain", "narrative",
    "liquidity_text", "degraded_text", "lead_axis_text", "rank_badge",
]

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


def sigma_words(z_bp: int | None) -> str:
    """z 를 사람 말로. 🔒 '좋다/나쁘다' 가 아니라 **높다/낮다** 로만 말한다.

    🔴 "압도적으로" 를 쓰지 않는다(2026-09-14 · ADR-SC-0013) — 무엇 대비 얼마나인지 말하지 않는
       낱말이다. 가장 높은 구간은 **숫자로** 말한다. 에이전트의 guard 가 그 낱말을 거부한다.
    """
    if z_bp is None:
        return "잴 수 없다"
    sigma = abs(z_bp) / 10000
    side = "높다" if z_bp > 0 else "낮다"
    if sigma >= 2.0:
        return f"다른 섹터들보다 2σ 이상 {side}"
    if sigma >= 1.0:
        return f"다른 섹터들보다 뚜렷이 {side}"
    if sigma >= 0.4:
        return f"다른 섹터들보다 다소 {side}"
    return "다른 섹터들과 비슷하다"


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
) -> list[str]:
    """이 섹터가 왜 이 자리인지를 **문단으로** 설명한다. 문장 목록을 돌려준다."""
    lines: list[str] = []

    # ① 어디에 있나
    if score_bp is None or rank is None:
        lines.append(
            f"**{label}**{josa(label, '은는')} 점수를 낼 수 없었다. "
            f"아래 표의 빈 칸이 이유다.")
        return lines
    lines.append(
        f"**{label}**{josa(label, '은는')} {total}개 섹터 중 **{rank}위**다. "
        f"점수 {score_bp / 10000:+.2f}σ 는 '{sigma_words(score_bp)}' 는 뜻이다."
    )

    scored = [p for p in parts if p["contribution_bp"] is not None]
    if scored:
        # ② 무엇이 밀어올렸나
        best = max(scored, key=lambda p: p["contribution_bp"])
        if best["contribution_bp"] > 0:
            name = AXIS_NAMES[best["axis"]]
            lines.append(
                f"이 자리를 만든 것은 주로 **{name}**{_ida(name)} — "
                f"이 축에서 {total}개 중 **{best['rank']}위**다. "
                f"{axis_plain(best['axis'], best['raw_bp'])}. "
                f"이 축 하나가 총점에 **{best['contribution_bp']:+d}** 만큼 보탰다."
            )
        # ③ 무엇이 깎았나
        worst = min(scored, key=lambda p: p["contribution_bp"])
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
