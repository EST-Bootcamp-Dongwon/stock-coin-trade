"""공유 링크 — URL 쿼리 파라미터 <-> 랭킹 화면 상태. **순수 함수다.**

## 🔴 URL 은 팀원이 링크로 받는 **외부 글**이다

ADR-SC-0012 4번이 "사람이 쓴 글" 로 묶은 목록에 쿼리 파라미터를 더한다. 값은 전부
화이트리스트로 받고, 쓸 수 없었던 것은 **키 이름만** 말한다. `Parsed` 의 필드가
그것을 타입으로 강제한다 — 화면이 실수로 외부 글자를 그릴 수 없다.

## 🔒 규칙 한 줄 — **URL 은 위젯의 초기값만 정한다**

화면이 실제로 쓰는 것만 화면이 말한다. 그래서 쓸 수 없는 파라미터를 기본값으로
두는 것은 "값을 지어내기"(절대 제약 8)가 아니다 — 화면은 실제로 그 프리셋, 그
필터를 쓰고 캡션이 그렇게 적는다. 반대로 **기준일은 지어낼 수 없다** — 링크의
`as_of` 가 지금 표와 다르면 화면이 그 사실을 말한다.

## 🔴 `bind="query-params"` 를 쓰지 않는다 — 실측 근거 셋 (streamlit 1.63.0)

1. URL 값이 **`format_func` 이 만든 표시 문자열**이 된다 — `?profile=모멘텀 55/25/15/5`.
   내부 id 를 넣으면 거부된다. 즉 URL 계약이 `explain.py` 의 문구에 묶이고, 라벨 한
   글자를 고치면 팀에 뿌린 링크가 전부 죽는다.
2. 잘못된 값을 **조용히 지운다**(`session_state._clear_url_param`).
3. 그래서 "무시했다" 보고를 그 위에 덧붙일 수 **없다** — 위젯 등록이 파라미터를 먼저
   지워 사용자 코드가 원본을 못 본다.

## 🔴 주소창은 **버튼을 누른 그 순간에만** 화면과 같다

`st.query_params` 쓰기는 프런트엔드에서 `history.pushState` 가 된다. 매 rerun 자동
갱신은 ① 클릭마다 히스토리를 쌓아 뒤로가기로 앱을 떠날 수 없게 하고 ② 뒤로가기 뒤에
주소창과 화면이 어긋나는데 **백엔드가 그것을 알 방법이 없다** — 같은 페이지 rerun 에서는
URL 을 다시 읽지 않는다(`script_runner` 는 페이지가 바뀔 때만 `populate_from_query_string`).

만들 수 없는 것("주소창이 항상 진실")을 약속하면 공유 기능이 거짓말을 하게 된다.
그래서 **추적한다고 가르치지 않는다** — 버튼을 누를 때만 쓰고, 버튼 문구가 그 계약을 말한다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Mapping, Sequence

from dashboard.weights import MAX_WEIGHT
from sector.scoring import AXES

__all__ = ["ParamKey", "Share", "Parsed", "parse", "encode", "with_hidden_sector"]

#: 🔒 우리가 아는 키 **전부**. `Parsed.ignored` 가 이 타입이라 외부 글자가 들어갈 수 없다.
ParamKey = Literal["profile", "w", "gics", "illiquid", "single", "sector", "as_of"]

_KEYS: tuple[ParamKey, ...] = ("profile", "w", "gics", "illiquid", "single",
                               "sector", "as_of")

#: 🔒 축 가중치의 URL 모양 — `M35-F30-B20-V15`. 퍼센트 인코딩이 붙지 않는 글자만 쓴다
#:    (`:` 나 `,` 는 `urlencode` 가 `%3A`·`%2C` 로 바꿔 링크가 읽히지 않는다).
#:    🔴 축 글자를 함께 담는 이유 — 순서만으로 적으면 손으로 쓴 링크의 **순서 착오를
#:    검출할 수 없다.** 축이 빠지거나 겹치면 `w` 를 통째로 거절한다.
_AXIS_SEPARATOR = "-"


@dataclass(frozen=True, slots=True)
class Share:
    """랭킹 화면의 공유 가능한 상태. 🔒 모든 필드가 화이트리스트를 통과한 **우리 값**이다.

    🔴 `custom` 과 `weights` 를 **따로** 둔다. 슬라이더를 켠 채 네 축을 전부 0 으로
       내린 상태는 도달 가능하고(그때 화면은 오류를 내고 프리셋으로 그린다),
       `weights=None` 하나로 접으면 "슬라이더를 안 쓴다" 와 구별되지 않아 링크가
       화면을 재현하지 못한다. 전부-0 판정은 화면(`Weighting.of`)이 한다.
    """

    profile: str
    custom: bool = False
    weights: Mapping[str, int] | None = None
    gics: frozenset[str] = frozenset()
    hide_illiquid: bool = False
    hide_single_etf: bool = False
    sector: str | None = None
    as_of: str | None = None


@dataclass(frozen=True, slots=True)
class Parsed:
    """파싱 결과. 🔒 **외부 문자열이 타입으로 없다.**

    - `ignored` — 우리가 아는 키인데 쓸 수 없었던 것. 이름만 담는다
    - `hidden_sector` — 프레임에 실재하지만 **필터에 가려진** 섹터 id. 🔒 쓰레기와
      다른 사유다. 우리 프레임에서 온 우리 값이라 화면이 이름을 말해도 된다
    - `unknown` — 모르는 키의 **개수**. 🔴 이름도 외부 글이라 세기만 한다
    - `stale_as_of` — 링크가 가리키는 기준일이 지금 표와 다르면 그 날짜.
      🔒 링크에서 온 글자지만 **실제로 존재하는 날짜**(`strptime`)여야만 여기 들어온다.
      🔴 `len==8 and isdigit()` 로는 부족했다 — `"00000000"`·`"٣٥٦٧٨٩٠١"`(아랍 숫자)·
      `"２０２６０９０９"`(전각)이 전부 통과해 화면에 그려졌다(2026-09-18 적대적 리뷰)
    - `stale_is_ahead` — 링크가 **지금 표보다 앞선** 날짜를 가리키는가. 화면이 어느
      방향으로 어긋났는지 말해야 하기 때문이다
    """

    share: Share
    ignored: tuple[ParamKey, ...] = ()
    hidden_sector: str | None = None
    unknown: int = 0
    stale_as_of: str | None = None
    stale_is_ahead: bool = False


def parse(raw: Mapping[str, Sequence[str]], *, profiles: Sequence[str],
          gics_ids: Sequence[str], sector_ids: Sequence[str],
          as_of: str, default_profile: str) -> Parsed:
    """URL 파라미터 -> `Parsed`. 🔒 못 쓰는 값은 **기본값으로 두고 키 이름을 남긴다.**

    `raw` 는 `{키: [값, ...]}` — 반복 파라미터(`?gics=A&gics=B`)를 잃지 않으려면
    `st.query_params.get_all` 로 모아 넘겨야 한다. `dict(st.query_params)` 는 마지막
    값만 준다(`QueryParams.__getitem__` 이 `value[-1]`).

    🔒 `sector_ids` 는 **언제나 그날 프레임에서** 낸다. `sectors.yaml` 전체를 넘기면
       프레임에 없는 id 가 통과해 `latest.loc[...]` 가 비고, 에이전트가 근거의 출처로
       적을 열이 없어진다 (ADR-SC-0013 4-1).
    """
    ignored: list[ParamKey] = []
    known = set(_KEYS)
    unknown = sum(1 for key in raw if key not in known)

    profile = _one(raw, "profile")
    if profile is None:
        chosen_profile = default_profile
    elif profile in profiles:
        chosen_profile = profile
    else:
        chosen_profile = default_profile
        ignored.append("profile")

    weights = _weights(_one(raw, "w"), ignored)

    gics = _subset(raw.get("gics"), gics_ids, ignored, "gics")

    hide_illiquid = _flag(_one(raw, "illiquid"), ignored, "illiquid")
    hide_single = _flag(_one(raw, "single"), ignored, "single")

    sector = _one(raw, "sector")
    if sector is not None and sector not in sector_ids:
        sector = None
        ignored.append("sector")

    link_day = _one(raw, "as_of")
    stale: str | None = None
    stale_ahead = False
    if link_day is not None:
        if not _is_day(link_day):
            ignored.append("as_of")
            link_day = None
        elif link_day != as_of:
            # 🔴 옛 날짜를 그리지 않는다 — 그럴 데이터 경로가 없다. **말할 뿐이다**
            stale = link_day
            # 🔒 방향까지 판정한다. `bas_dd` 는 사전순 = 날짜순이라 `<` 한 글자다.
            #    🔴 방향을 안 재면 "지금 표는 그 뒤의 기준일이다" 가 거짓이 되는 경우가
            #    실재한다 — `load_scores` 가 HF→로컬로 폴백하면 **내 표가 더 옛날**일 수 있다
            stale_ahead = link_day > as_of

    return Parsed(
        share=Share(profile=chosen_profile, custom=weights is not None,
                    weights=weights, gics=gics, hide_illiquid=hide_illiquid,
                    hide_single_etf=hide_single, sector=sector, as_of=link_day),
        ignored=tuple(ignored), unknown=unknown, stale_as_of=stale,
        stale_is_ahead=stale_ahead,
    )


def with_hidden_sector(parsed: Parsed, keep: frozenset[str]) -> Parsed:
    """링크의 섹터가 링크의 **필터에 가려졌는지** 판정해 새 `Parsed` 를 만든다.

    🔴 `parse` 에서 함께 할 수 없다 — 가려짐 판정은 필터가 정해진 뒤에야 가능하고,
       필터는 같은 URL 에서 온다. 그래서 두 단계이고, 둘 다 순수 함수다.

    🔒 "쓰레기 문자열" 과 **다른 사유**다. 여기 걸린 id 는 우리 프레임에서 왔으므로
       화면이 이름을 말하고 필터를 풀 길을 줄 수 있다.
    """
    sector = parsed.share.sector
    if sector is None or sector in keep:
        return parsed
    return replace(parsed, share=replace(parsed.share, sector=None),
                   hidden_sector=sector)


def encode(share: Share) -> dict[str, str | list[str]]:
    """`Share` -> URL 파라미터. 🔒 **기본값은 넣지 않는다** — 링크가 읽혀야 한다.

    🔒 `profile` 은 **항상** 넣는다. 빼면 "기본 프리셋" 이라는 뜻이 되고, 나중에 기본을
       바꾸는 순간 팀에 뿌린 옛 링크의 의미가 조용히 변한다.

    🔒 `gics` 를 `sorted()` 로 정규화한다 — 같은 화면이 같은 링크를 내야 한다.
    """
    out: dict[str, str | list[str]] = {"profile": share.profile}
    if share.custom and share.weights is not None:
        out["w"] = _AXIS_SEPARATOR.join(f"{a}{share.weights[a]}" for a in AXES)
    if share.gics:
        out["gics"] = sorted(share.gics)
    if share.hide_illiquid:
        out["illiquid"] = "1"
    if share.hide_single_etf:
        out["single"] = "1"
    if share.sector:
        out["sector"] = share.sector
    if share.as_of:
        out["as_of"] = share.as_of
    return out


# -- 조각들 -----------------------------------------------------------------

#: 🔒 가중치 한 축의 자릿수 상한. `MAX_WEIGHT` 가 100 이므로 3자리면 충분하다.
_MAX_DIGITS = 3


def _is_number(text: str) -> bool:
    """**ASCII 10진 숫자**인가. 🔴 `str.isdigit()` 을 쓰지 않는다 — 실측(2026-09-18):

    - `"²".isdigit()` 은 `True` 인데 `int("²")` 는 `ValueError` 다. `parse` 는 순수 함수라
      아무도 안 잡아 주고, 적용이 **모든 위젯보다 앞**이라 `?w=M²-F30-B20-V15` 한 줄이
      **페이지를 한 칸도 못 그리게 만들었다.**
    - `"٣٥".isdigit()`·`.isdecimal()` 이 둘 다 `True` 이고 `int` 가 35 를 준다 —
      크래시는 아니지만 **조용히 다른 표기를 받아들인다.** `bind="query-params"` 를
      기각한 이유가 조용한 교정이었으므로 여기서도 받지 않는다.
    - 파이썬 3.12 는 **4300자리 초과** 정수 변환을 `ValueError` 로 막는다. 그래서
      자릿수를 먼저 잰다 — `int()` 에 닿기 전에 거른다.
    """
    return text.isascii() and text.isdecimal() and 0 < len(text) <= _MAX_DIGITS


def _one(raw: Mapping[str, Sequence[str]], key: str) -> str | None:
    """반복된 파라미터의 **마지막** 값. 🔒 Streamlit 의 매핑 접근과 같은 규칙이다."""
    values = raw.get(key)
    if not values:
        return None
    return str(values[-1])


def _is_day(text: str) -> bool:
    """`YYYYMMDD` 이고 **실제로 존재하는 날짜**인가. 🔒 모양만 보지 않는다."""
    from datetime import datetime

    if not (text.isascii() and text.isdecimal() and len(text) == 8):
        return False
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return False
    return True


def _flag(value: str | None, ignored: list[ParamKey], key: ParamKey) -> bool:
    """`"1"`/`"0"` 만 받는다. 🔴 `bool(value)` 로 읽으면 `?illiquid=0` 이 필터를 **켠다**."""
    if value is None:
        return False
    if value == "1":
        return True
    if value == "0":
        return False
    ignored.append(key)
    return False


def _subset(values: Sequence[str] | None, allowed: Sequence[str],
            ignored: list[ParamKey], key: ParamKey) -> frozenset[str]:
    """허용 목록에 있는 것만. 🔒 하나라도 버렸으면 키 이름을 남긴다."""
    if not values:
        return frozenset()
    allow = set(allowed)
    kept = {str(v) for v in values if str(v) in allow}
    if len(kept) != len({str(v) for v in values}):
        ignored.append(key)
    return frozenset(kept)


def _weights(value: str | None, ignored: list[ParamKey]) -> dict[str, int] | None:
    """`M35-F30-B20-V15` -> `{"M":35,...}`. 🔒 **전부-또는-무효다.**

    🔴 여기서 `weights.normalize` 를 부르지 않는다 — 네 축을 전부 0 으로 내린 상태는
       슬라이더로 도달 가능하고, 그때 화면은 오류를 내고 프리셋으로 그린다. 파싱 단계가
       그것을 거절하면 링크가 그 화면을 재현하지 못한다. 전부-0 판정은 화면이 한다.
    """
    if value is None:
        return None
    out: dict[str, int] = {}
    for token in value.split(_AXIS_SEPARATOR):
        axis, digits = token[:1], token[1:]
        if axis not in AXES or axis in out or not _is_number(digits):
            ignored.append("w")
            return None
        number = int(digits)
        if not 0 <= number <= MAX_WEIGHT:
            ignored.append("w")
            return None
        out[axis] = number
    if set(out) != set(AXES):
        ignored.append("w")
        return None
    return out
