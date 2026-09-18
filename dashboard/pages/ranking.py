"""섹터 랭킹 — "어느 섹터인가".

🔒 **가중치 프리셋 셋을 동시에** 보여준다. 하나만 보면 가중치를 바꿔 아무 섹터나
   1위로 만들 수 있다 (계획서 R11).

## 🔴 슬라이더는 **민감도를 보는 도구**다 — 새 관점이 아니다 (M9 · 2026-09-17)

가중치를 직접 고를 수 있게 하되, 그 상태에서는 **근거·확정 칸을 닫는다.**

에이전트는 근거의 출처를 `("column", "rank_balanced")` 처럼 **저장된 열 이름**으로
적고 guard 가 그 열에서 값을 다시 얻어 문장의 숫자를 대조한다(ADR-SC-0013 ④-1).
이름 없는 가중치에는 그런 열이 없다. guard 를 재계산 함수로 바꾸면 "원천에서 따로
다시 얻는다" 가 "같은 함수를 두 번 부른다" 가 되어 대조가 대조이기를 그친다.

🔒 대신 되돌아가는 길을 **한 번의 클릭**으로 만든다. 그리고 슬라이더를 프리셋과
   같은 비율로 맞추면 `Weighting.of` 가 알아서 프리셋으로 돌린다 —
   70/60/40/30 은 35/30/20/15 와 점수가 한 칸도 다르지 않기 때문이다.

## 🔴 필터는 **행을 숨길 뿐** 점수를 다시 매기지 않는다

순위·z·축 순위는 언제나 그날 21개 섹터 횡단면에서 나온 값이다. 고른 범위 안에서
다시 매기면 "F 축 1위" 가 "고른 다섯 중 1위" 가 되고, 화면은 그 차이를 말하지 않는다.
그래서 걸러진 집합은 `only=` 로만 넘기고 계산에는 **원본 프레임**이 들어간다.

## 🔒 하단(`evidence` · `team_actions`)에는 **언제나 원본 프레임**을 넘긴다

`sector_story` 의 `total` 이 곧 "21개 중 3위" 의 21 이다. 걸러진 프레임이 새면
그 문장이 "5개 중 1위" 가 된다.

## 🔴 점수 없는 섹터는 **정상**이다 — 그리다 죽지 않는다 (이슈 #3)

파생본의 정수 열은 nullable `Int64` 이고, 창이 안 찬 초기 영업일과 **새로 넣은 섹터**는
점수·순위가 `pd.NA` 다. 실제로 `score_daily.parquet` 5985행 중 399행이 그렇다.

🔒 그 칸을 `int()` 로 캐스팅하거나 비교에 넣으면 `TypeError` 로 **페이지가 통째로
   죽는다** — 한 섹터의 빈칸이 나머지 20개까지 가린다. 칸은 `view.int_or_none` 으로
   내려 받고, 등수는 `rank_badge` 가 `—` 로 그린다 (ADR-SC-0007).
🔒 `nsmallest` 는 결측을 **버리지 않는다** — 순위가 있는 섹터가 `n` 보다 적으면 `pd.NA`
   행으로 채워 돌려준다(pandas 3.0.5 실측). 그래서 세기 전에 `notna()` 로 거른다.

## 🔗 공유 링크 — 읽기는 첫 로드에만, 쓰기는 **버튼을 누를 때만** (M9c)

URL 의 규칙은 한 줄이다 — **URL 은 위젯의 초기값만 정한다. 화면이 실제로 쓰는 것만
화면이 말한다.** 그래서 쓸 수 없는 파라미터를 기본값으로 두는 것은 값을 지어내기가
아니다(캡션이 실제로 쓰는 프리셋·필터를 적는다). 대신 **기준일은 지어낼 수 없다** —
링크의 `as_of` 가 지금 표와 다르면 말한다.

🔒 파싱·인코딩은 전부 `dashboard/share.py` 다. 여기서는 **세션에 심고 그리기만** 한다.
🔒 심는 것은 `_weight_controls` 보다 **먼저**다 — 위젯이 그려진 뒤에 그 키를 쓰면
   `StreamlitWidgetAlreadyInstantiatedError` 가 난다(`_sync_sliders` 와 같은 규율).
   `rank_detail` 까지 여기서 심으므로 `keep` 은 `view.visible_ids` 로 **위젯 없이** 낸다.
🔴 자동 갱신을 하지 않는 이유는 `share.py` 머리주석에 있다 — 요약하면 이 플랫폼에서
   "주소창이 항상 진실" 은 만들 수 없고, 만들 수 없는 것을 약속하면 공유가 거짓말이 된다.
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, evidence, share, team_actions, theme, view, weights
from dashboard.explain import AXIS_NOT, lead_axis_text, preset_label, rank_badge, weighting_label
from sector.scoring import AXES, AXIS_NAMES, PRESETS

_STABILITY_DAYS = 20

#: 슬라이더 위젯 키. 🔒 콜백이 **위젯을 그리기 전에** 이 키에 쓴다 — 그린 뒤에 쓰면
#:    Streamlit 이 `StreamlitWidgetAlreadyInstantiatedError` 를 던진다
#:    (`evidence._reset_on_new_sector` 와 같은 규율).
_SLIDER_KEYS = {axis: f"rank_w_{axis}" for axis in AXES}

CUSTOM_NOTICE = (
    "가중치를 직접 고쳤다. 이 상태에서는 **설명 문장과 확정 칸을 열지 않는다** — "
    "설명에 쓰는 숫자는 게시된 프리셋 열에서 다시 얻어 대조하는데, 직접 고른 비율에는 "
    "그 열이 없기 때문이다. 조원이 붙인 근거는 **조** 페이지의 기록에서 그대로 읽을 수 있다."
)

#: 🔒 링크를 한 번 적용했다는 표시. 이것이 없으면 위젯을 만질 때마다 URL 이 다시
#:    이겨서 링크로 들어온 사람이 **아무것도 바꿀 수 없다**.
_APPLIED = "sc_share_applied"

#: 🔒 "쓸 수 없었던 것" 보고는 **URL 이 고쳐질 때까지** 남는다 — 첫 로드에만 띄우면
#:    사용자가 읽기 전에 아무 위젯이나 누르는 순간 사라진다. 주소창에 그 파라미터가
#:    아직 있으므로 계속 말하는 것이 사실에 맞다.
_REPORT = "sc_share_report"

SHARE_HELP = ("누르면 **주소창이 지금 이 화면 그대로** 바뀐다. 그 주소를 복사해 보내면 된다. "
              "🔒 누르기 전까지 주소창은 처음 열었을 때 그대로다 — 화면을 따라가지 않는다.")

LIQUIDITY_HELP = ("일평균 거래대금 1억 미만인 섹터만 숨긴다. "
                  "🔒 창이 덜 차서 **판정할 수 없는** 섹터는 남는다 — 모르는 것과 미달인 것은 다르다.")


def render() -> None:
    theme.header("섹터 랭킹", "21개 한국 테마 섹터를 네 가지 각도로 재서 줄 세운다")
    source_label = ""
    try:
        try:
            frame, source = data.load_scores()
        except data.DataUnavailable as exc:
            # 🔴 가짜 숫자를 그리지 않는다. 무엇을 해야 하는지 말하고 끝낸다
            # 🔒 링크 보고도 하지 않는다 — 화이트리스트가 **프레임에 의존**해서
            #    (`gics_ids`·`sector_ids`·`as_of`) 프레임 없이 파싱하면 멀쩡한 `gics`·
            #    `sector` 까지 "못 썼다" 고 말한다. 틀린 보고보다 침묵이 낫고,
            #    `_APPLIED` 를 세우지 않았으므로 다음 성공 로드에서 제대로 적용된다
            st.error(str(exc))
            return
        source_label = source.label
        if source.is_local:
            st.info(f"⚠️ {source.label}")

        as_of = data.latest_day(frame)
        total = len(view.latest_frame(frame))
        names = data.sector_names()

        # 🔒 **위젯보다 먼저.** 그린 뒤에 위젯 키를 쓰면 Streamlit 이 던진다 (머리주석)
        _apply_link(frame, as_of=as_of, names=names)

        weighting, profile = _weight_controls()
        keep = _filters(frame, names)

        st.caption(
            f"기준일 **{as_of}** · 섹터 {total}개 중 **{len(keep)}개 표시** · "
            f"{weighting_label(weighting)} · "
            f"정의 `{view.latest_frame(frame)['config_version'].iloc[0]}`"
        )
        _report_link(names)
        if not weighting.is_preset:
            st.info(CUSTOM_NOTICE)
            # 🔒 라벨이 **실제로 돌아갈 곳**을 말한다. 위에서 고른 프리셋이지 균형이 아니다
            st.button(f"{preset_label(profile)} 로 돌아가기", key="rank_back",
                      on_click=_leave_custom, args=(profile,))

        ranked = view.scored(frame, weighting)
        if not keep:
            st.markdown("<div class='sc-warn'>고른 조건에 맞는 섹터가 없다. "
                        "필터를 풀면 다시 보인다.</div>", unsafe_allow_html=True)
            # 🔒 이 화면도 공유할 만하다 — "내 필터로는 아무것도 안 남는다" 가 사실이다.
            # 🔴 `sector=None` 이다 — selectbox 를 그리지 않았으니 **그 run 이 그린 값이 없다.**
            #    ⚠️ 실측(streamlit 1.63.0 · 이 앱): 이 run 에서는 `rank_detail` 이
            #    `session_state` 에서 **아예 사라진다.** 그래서 세션을 읽는 코드로 바꿔도
            #    지금은 결과가 같다 — 즉 **테스트가 그 차이를 볼 수 없다**(돌연변이 검사로
            #    확인). 그 사실 자체를 `test_필터로_아무것도_안_남는_화면도_공유된다` 가
            #    단언하므로, 앞으로 세션값이 남게 바뀌면 그 테스트가 먼저 깨진다
            _share_button(as_of=as_of, sector=None, key="rank_share_empty")
            return

        st.subheader("상위 3" if len(keep) == total else "고른 범위에서 가장 높은 셋")
        _render_podium(ranked, weighting, names, keep)

        st.subheader("순위")
        table = view.ranking_table(ranked, days=_STABILITY_DAYS, names=names)
        _render_table(table, keep)

        st.subheader("점수를 한눈에")
        _render_bars(ranked, names, keep, partial=len(keep) != total)

        st.subheader("셋 다 상위인 섹터")
        _render_consensus(frame, names)

        st.subheader("대분류별로 어디에 있나")
        _render_gics_distribution(ranked, names, keep)

        st.subheader("왜 이 점수인가")
        shown = [sid for sid in table.index if sid in keep]
        chosen = st.selectbox(
            "섹터", shown, key="rank_detail",
            # 🔒 순위가 없는 섹터는 `— · 이름` 이다. `int()` 로 캐스팅하면 그 한 줄이
            #    셀렉트박스를 죽이고 페이지 전체가 사라진다 (머리주석 · 이슈 #3)
            format_func=lambda sid: f"{rank_badge(view.int_or_none(table.loc[sid, '순위']))}"
                                    f" · {names.sector_full(sid)}",
        )
        # 🔒 **그 run 이 실제로 그린 값**을 넘긴다 — 세션이 아니다 (조기 return 경로 참고)
        _share_button(as_of=as_of, sector=chosen, key="rank_share")

        if chosen and weighting.is_preset:
            # 🔒 **원본 프레임**을 넘긴다 (머리주석) — 걸러진 것도, `scored()` 를 지난
            #    것도 아니다. 에이전트는 저장된 프리셋 열을 스스로 다시 읽는다
            _render_breakdown(frame, chosen, weighting.name, names)
            # ★ 확정 · 근거 붙이기는 **근거를 읽은 바로 그 자리**에서 한다 (ADR-SC-0012 ②)
            team_actions.render_sector_actions(frame, chosen, profile=weighting.name,
                                               names=names)
    finally:
        theme.footer(source_label)


# ── 공유 링크 ───────────────────────────────────────────────────────────────
# 🔒 파싱·인코딩은 `share.py` 다. 여기는 **세션에 심고 그리기만** 한다 (머리주석).

def _raw_params() -> "dict[str, list[str]]":
    """지금 URL 의 파라미터. 🔴 **`get_all` 로 모은다.**

    `dict(st.query_params)` 는 반복 파라미터를 **마지막 값으로 접는다**
    (`QueryParams.__getitem__` 이 `value[-1]`). 그러면 `?gics=A&gics=B` 가 B 하나가 된다.
    """
    return {key: list(st.query_params.get_all(key)) for key in st.query_params}


def _apply_link(frame, *, as_of: str, names: view.Names) -> None:
    """링크의 값을 위젯 **초기값으로** 심는다. 🔒 **첫 로드에만.**

    🔴 매 rerun 적용하면 URL 이 위젯을 계속 이겨서 링크로 들어온 사람이 아무것도
       바꿀 수 없다. 그래서 세션 플래그로 한 번만 돈다.

    ## 🔴 플래그는 **무조건** 세운다 — 조건을 달았다가 더 나쁜 버그를 만들었다

    한때 "프레임 때문에 무시된 것이 있으면 세우지 않는다" 로 두었다. 근거는
    화이트리스트가 프레임에 의존하고(`gics_ids`·`sector_ids`·`as_of`) 프레임이 세션
    안에서 바뀔 수 있다는 것이었다 — `load_scores` 캐시 TTL(300초)과 HF→로컬 폴백.

    🔴 그 조건이 **`?sector=<없는id>` 한 줄로 그 세션의 모든 위젯을 영구히 잠갔다.**
       플래그가 없으니 매 rerun 마다 URL 이 위젯 키에 다시 심기고, 라디오·멀티셀렉트·
       체크박스를 몇 번 눌러도 되돌아온다. 오타 하나, 슬랙에서 잘린 링크 하나,
       어제 있던 섹터 id 하나로 충분하다. 더 나쁜 변종은 `hidden_sector` 경로다 —
       화면이 "필터를 풀면 나온다" 고 **불가능한 행동을 지시한다**(필터가 매 rerun
       다시 켜진다). 적대적 구현 리뷰가 재현했다.

    🔒 지키려던 드문 경우(폴백된 옛 파생본)의 탈출구는 **새로고침**이다 — 링크는
       주소창에 그대로 있고, 새 세션에서 신선한 프레임에 대고 다시 판정된다.
       잠긴 위젯에는 탈출구가 없다. 드문 것을 흔한 것보다 앞세우지 않는다.
    """
    if st.session_state.get(_APPLIED):
        return
    # 🔒 **무엇을 만나도 여기서 한 번만 돈다** (머리주석). 🔴 파라미터가 없을 때와
    #    Streamlit 이 쿼리를 통째로 지웠을 때(파라미터 1000개·512Ki 초과)를 **구별할 수
    #    없다** — 그때는 아무 말도 못 한다. ADR-SC-0015 가 한계로 적는다
    st.session_state[_APPLIED] = True
    raw = _raw_params()
    if not raw:
        return

    latest = view.latest_frame(frame)
    parsed = share.parse(
        raw,
        profiles=view.PROFILES,
        gics_ids=[gics for gics, _ in view.gics_options(frame, names)],
        # 🔒 **그 프레임의 섹터만.** `sectors.yaml` 전체를 넘기면 프레임에 없는 id 가
        #    통과해 에이전트가 근거의 출처로 적을 열이 없어진다 (ADR-SC-0013 ④-1)
        sector_ids=[str(v) for v in latest["sector_id"]],
        as_of=as_of,
        default_profile=view.PROFILES[0],
    )
    # 🔒 가려짐 판정은 필터가 정해진 뒤에야 가능하고, 필터도 같은 URL 에서 온다.
    #    🔴 `view.visible_ids` 는 **순수 함수라 위젯을 그리지 않는다** — 그래서 여기서
    #    `rank_detail` 까지 심을 수 있고, 정적 검사의 허용 사유가 "모든 위젯보다 앞"
    #    하나로 정직해진다
    keep = view.visible_ids(frame, gics=parsed.share.gics,
                            hide_illiquid=parsed.share.hide_illiquid,
                            hide_single_etf=parsed.share.hide_single_etf)
    parsed = share.with_hidden_sector(parsed, keep)

    picked = parsed.share
    st.session_state["rank_profile"] = picked.profile
    st.session_state["rank_custom"] = picked.custom
    if picked.weights is not None:
        for axis, key in _SLIDER_KEYS.items():
            st.session_state[key] = picked.weights[axis]
    st.session_state["rank_gics"] = sorted(picked.gics)
    st.session_state["rank_hide_illiquid"] = picked.hide_illiquid
    st.session_state["rank_hide_single"] = picked.hide_single_etf
    if picked.sector is not None:
        st.session_state["rank_detail"] = picked.sector

    st.session_state[_REPORT] = parsed


def _report_link(names: view.Names) -> None:
    """링크에서 **쓸 수 없었던 것**을 말한다. 🔒 우리 문장 + 우리 키 이름뿐이다.

    🔴 모르는 키는 **개수만** 센다 — 키 이름도 외부 글이고, `st.warning` 은 마크다운을
       읽는다(ADR-SC-0012 ④ 가 위젯 라벨·`st.error` 에 사람 글을 넣지 말라고 한 이유).

    🔒 `hidden_sector` 만 **이름을 말한다.** 그 id 는 우리 프레임에서 왔고, 사용자가
       필터를 풀어야 볼 수 있다는 것을 알려야 링크가 목적을 잃지 않는다.
    """
    parsed = st.session_state.get(_REPORT)
    if parsed is None:
        return
    if parsed.stale_as_of:
        # 🔴 방향을 말한다. 안 재면 거짓이 되는 경우가 실재한다 — `load_scores` 가
        #    HF→로컬로 폴백하면 **내 표가 링크보다 옛날**일 수 있다
        where = ("지금 표가 그보다 **옛날**이다 — 새 파생본을 못 읽었다"
                 if parsed.stale_is_ahead else "지금 표는 그 뒤의 기준일이다")
        st.warning(f"⏳ 이 링크는 **{parsed.stale_as_of}** 기준으로 만들어졌고, {where}. "
                   "순위와 점수가 그때와 다를 수 있다.")
    if parsed.hidden_sector is not None:
        st.warning(f"🔎 링크가 가리킨 **{names.sector_full(parsed.hidden_sector)}** 은 "
                   "이 링크의 필터에 가려져 있다. 필터를 풀면 아래 목록에 나온다.")
    if parsed.ignored:
        keys = " · ".join(f"`{key}`" for key in parsed.ignored)
        st.warning(f"🔗 링크의 {keys} 값을 쓸 수 없어 **기본값으로 열었다.** "
                   "화면 위 캡션이 지금 실제로 쓰는 가중치와 필터를 적고 있다.")
    if parsed.unknown:
        st.warning(f"🔗 링크에 모르는 파라미터 **{parsed.unknown}개**가 있어 무시했다.")


def _share_button(*, as_of: str, sector: str | None, key: str) -> None:
    """주소창을 **지금 이 화면**으로 맞춘다. 🔒 누를 때만 쓴다 (머리주석 · `share.py`).

    🔴 `Weighting` 이 아니라 **슬라이더 세션값**을 담는다. 네 축을 전부 0 으로 내리면
       `_weight_controls` 가 `WeightError` 를 잡아 프리셋으로 그리는데, 그때
       `Weighting.weights` 는 프리셋 값이라 화면의 슬라이더와 다르다. 링크는 **화면**을
       재현해야 한다. 그래서 `share.Share` 가 `custom` 과 `weights` 를 따로 든다.

    🔒 `as_of` 는 **인자로 받는다.** 링크는 "이 화면을 언제 봤나" 를 기록하고, 받는
       사람의 표가 그 뒤의 날이면 `_report_link` 가 그 사실을 말한다.
    """
    custom = bool(st.session_state.get("rank_custom"))
    current = share.Share(
        profile=str(st.session_state["rank_profile"]),
        custom=custom,
        weights={a: int(st.session_state[k]) for a, k in _SLIDER_KEYS.items()}
                if custom else None,
        gics=frozenset(st.session_state.get("rank_gics") or ()),
        hide_illiquid=bool(st.session_state.get("rank_hide_illiquid")),
        hide_single_etf=bool(st.session_state.get("rank_hide_single")),
        sector=sector,
        as_of=as_of,
    )
    if st.button("🔗 이 화면을 링크로 만들기", key=key, help=SHARE_HELP):
        st.query_params.from_dict(share.encode(current))
        # 🔒 URL 이 고쳐졌으므로 옛 링크에 대한 보고는 사실이 아니게 된다
        st.session_state.pop(_REPORT, None)
        st.success("주소창이 지금 이 화면 그대로다. 그 주소를 복사해 팀에 보내면 된다.")


# ── 가중치 ──────────────────────────────────────────────────────────────────

def _sync_sliders(profile: str) -> None:
    """슬라이더를 그 프리셋 값으로. 🔒 **콜백이다** — 위젯이 그려지기 전에 돌아야
    Streamlit 이 값을 받아 준다(`StreamlitWidgetAlreadyInstantiatedError`)."""
    for axis, key in _SLIDER_KEYS.items():
        st.session_state[key] = PRESETS[profile][axis]


def _leave_custom(profile: str) -> None:
    """슬라이더를 맞추고 '직접 고르기' 를 끈다 — 프리셋으로 돌아가는 길.

    🔴 **`profile` 을 받는다.** 예전에는 `"balanced"` 를 박아 두고 라디오를 안 건드려,
       라디오가 모멘텀인 상태에서 누르면 버튼은 "균형" 이라 말하고 캡션은 모멘텀
       55/25/15/5 를, 슬라이더는 균형 35/30/20/15 를 보여 줬다 — **지금 쓰는 가중치와
       화면에 적힌 가중치가 달랐다.** ADR-SC-0014 ④ 가 금지한 "화면의 거짓말" 이다.
    """
    _sync_sliders(profile)
    st.session_state["rank_custom"] = False


def _weight_controls() -> "tuple[weights.Weighting, str]":
    """프리셋 라디오 + 접어 둔 슬라이더.

    돌려주는 것은 **지금 쓰는 가중치**와 **라디오가 가리키는 프리셋** 둘이다 — 커스텀일 때
    돌아갈 곳이 어디인지 화면이 말해야 하기 때문이다(`_leave_custom`).
    """
    # 🔒 위젯을 그리기 **전에** 기본값을 심는다. `st.slider(value=...)` 와 `key=` 를 같이
    #    주면 세션값이 있는 매 rerun 마다 Streamlit 이 로거 경고를 남긴다(배포 로그가 더러워진다)
    for axis, key in _SLIDER_KEYS.items():
        st.session_state.setdefault(key, PRESETS["balanced"][axis])
    profile = st.radio(
        "가중치", list(view.PROFILES), horizontal=True,
        format_func=preset_label, key="rank_profile",
    )
    with st.expander("고급 — 가중치를 직접 고른다"):
        st.markdown(
            "<div class='sc-muted'>합이 100 이 아니어도 된다 — **비율만** 쓴다. "
            "그래서 70/60/40/30 은 35/30/20/15 와 점수가 같고, 화면도 그때는 "
            "균형 프리셋으로 돌아간다.</div>", unsafe_allow_html=True)
        custom = st.checkbox("직접 고른 값을 쓴다", key="rank_custom")
        for axis in AXES:
            st.slider(f"{AXIS_NAMES[axis]} ({axis})", 0, weights.MAX_WEIGHT,
                      key=_SLIDER_KEYS[axis], help=AXIS_NOT[axis])
        st.button(f"슬라이더를 {preset_label(profile)} 값으로 맞추기", key="rank_sync",
                  on_click=_sync_sliders, args=(profile,))
    if not custom:
        return weights.Weighting.preset(profile), profile
    try:
        return weights.Weighting.of(
            {a: st.session_state[_SLIDER_KEYS[a]] for a in AXES}), profile
    except weights.WeightError as exc:
        # 🔒 우리가 쓴 문장이다 — 사람이 쓴 글이 아니라 마크다운에 넣어도 된다
        st.error(f"{exc} — 프리셋으로 그린다.")
        return weights.Weighting.preset(profile), profile


# ── 필터 ────────────────────────────────────────────────────────────────────

def _filters(frame, names) -> frozenset[str]:
    """보일 섹터 id 집합. 🔴 **점수를 다시 매기지 않는다** — 행을 숨길 뿐이다."""
    options = view.gics_options(frame, names)
    labels = dict(options)
    picked = st.multiselect(
        "GICS 대분류", [gics for gics, _ in options], key="rank_gics",
        format_func=lambda g: labels.get(g, g),
        placeholder="전체 — 고르면 그 대분류만 남는다",
        help="🔒 대분류 점수를 따로 내지 않는다. 대분류 ETF 는 살 수 없어 매매 단위가 "
             "아니기 때문이다. 순위는 언제나 테마 21개 안에서의 자리다.")
    left, right = st.columns(2)
    with left:
        hide_illiquid = st.checkbox("유동성 미달 숨기기", key="rank_hide_illiquid",
                                    help=LIQUIDITY_HELP)
    with right:
        hide_single = st.checkbox("ETF 1종 섹터 숨기기", key="rank_hide_single",
                                  help="ETF 가 하나뿐이면 섹터 지수가 그 한 종목이다.")

    # 🔒 판정은 `view.visible_ids` 가 한다 — 여기는 위젯 값을 모으기만 한다.
    #    화면 없이 검증할 수 있어야 "판정 불가를 숨기지 않는다" 를 테스트로 고정한다
    return view.visible_ids(frame, gics=frozenset(picked),
                            hide_illiquid=hide_illiquid, hide_single_etf=hide_single)


# ── 그리기 ──────────────────────────────────────────────────────────────────

def _render_table(table, keep: frozenset[str]) -> None:
    shown = table.loc[[sid for sid in table.index if sid in keep]]
    st.dataframe(
        shown,
        width="stretch",
        column_config={
            # 🔴 숫자와 **막대를 같이** 준다. `12522` 는 즉시 못 읽지만 막대 길이는
            #    읽힌다. 🔒 눈금은 **걸러지기 전 21개**의 최소~최대다 — 필터로 눈금이
            #    좁아지면 같은 섹터의 막대 길이가 필터 유무로 달라지고 help 가 거짓이 된다
            "점수bp": st.column_config.ProgressColumn(
                "점수", help="Σ(가중치×z)/Σ가중치 × 10000. ±30000 = ±3σ. "
                            "막대는 이 날 21개 섹터의 최소~최대 안에서의 자리다",
                format="%d", min_value=_floor(table["점수bp"]),
                max_value=_ceil(table["점수bp"])),
            "평균순위": st.column_config.NumberColumn(
                f"최근{_window(table)}일 평균순위",
                help="🔴 '오늘만 1등' 과 '계속 1등' 을 가른다. "
                     "표본일이 이보다 작으면 그 섹터는 비워 둔다", format="%.1f"),
            "표본일": st.column_config.NumberColumn(
                "표본일", help="평균을 실제로 몇 영업일에서 냈는가", format="%d"),
            "진폭": st.column_config.NumberColumn(
                "진폭", help="순위 표준편차. 작을수록 꾸준하다", format="%.1f"),
            **{a: st.column_config.NumberColumn(
                f"{a} ({AXIS_NAMES[a]})", help=AXIS_NOT[a], format="%d") for a in AXES},
        },
    )
    st.markdown(
        "<div class='sc-muted'>축 값은 bp 다 — 10000 = 1.0σ. "
        "빈 칸은 값이 없다는 뜻이고 0 이 아니다. 순위는 걸러내기 전 21개 안에서의 "
        "자리라, 필터를 걸어도 번호가 이어지지 않는다.</div>",
        unsafe_allow_html=True,
    )


def _window(table) -> int:
    """표본일의 최댓값 = 실제로 볼 수 있었던 창. 🔒 요청한 20 을 그대로 쓰지 않는다."""
    days = table["표본일"].dropna()
    return int(days.max()) if len(days) else _STABILITY_DAYS


def _render_podium(frame, weighting, names, keep: frozenset[str]) -> None:
    """가장 높은 셋 — **등수를 크게, 이유를 옆에.**

    🔴 21행 표에서 1등을 눈으로 찾는 일이 개발자가 아닌 팀원에게는 진입 장벽이다.
    🔒 그렇다고 등수만 크게 그리지 않는다. "무엇이 끌어올렸나" 와 유동성 경고를
       같은 카드에 둔다 — 등수만 보이면 "1위 = 사면 오른다" 로 읽힌다.
    🔒 카드의 등수는 걸러내기 전 21개 안에서의 자리다. 필터를 걸면 "3위·11위·17위"
       처럼 이가 빠진 채로 나오는데, **그게 사실이다.**
    """
    entries = view.podium(frame, weighting=weighting, names=names, only=keep)
    if not entries:
        st.markdown("<div class='sc-muted'>순위를 낼 수 있는 섹터가 없다.</div>",
                    unsafe_allow_html=True)
        return
    for column, entry in zip(st.columns(len(entries)), entries, strict=True):
        with column, theme.panel():
            st.markdown(
                f"<div class='sc-rank'>{rank_badge(entry['rank'])}</div>"
                f"<div class='sc-rank-name'>{entry['label']}</div>",
                unsafe_allow_html=True)
            score = entry["score_bp"]
            st.markdown(f"**{score / 10000:+.2f}σ**" if score is not None
                        else theme.missing("점수 없음"), unsafe_allow_html=True)
            st.markdown(f"<div class='sc-muted'>{lead_axis_text(entry['lead_axis'])}</div>",
                        unsafe_allow_html=True)
            if entry["liquidity_ok"] is False:
                st.markdown("<div class='sc-warn'>🔴 유동성 미달</div>",
                            unsafe_allow_html=True)
            elif entry["liquidity_ok"] is None:
                st.markdown("<div class='sc-muted'>유동성 판정 불가 — 창이 덜 찼다</div>",
                            unsafe_allow_html=True)
            elif entry["etf_n"] == 1:
                st.markdown("<div class='sc-warn'>🔴 ETF 1종</div>",
                            unsafe_allow_html=True)


def _render_bars(frame, names, keep: frozenset[str], *, partial: bool) -> None:
    """섹터 점수를 가로 막대로. 🔒 순위 순서를 지킨다(`sort=False`).

    🔴 0 을 기준으로 좌우로 갈린다 — 음수 섹터가 왼쪽으로 뻗는 그림이 "평균보다
       아래" 를 표보다 빨리 말한다.
    """
    bars = view.score_bars(frame, names=names, only=keep)
    st.bar_chart(bars, horizontal=True, sort=False, height=460,
                 x_label="점수(σ) — 0 이 21개 섹터의 가운데다", y_label="")
    tail = (" 필터를 걸었으므로 **가로축은 지금 보이는 섹터들에 맞춰 다시 잡힌다** — "
            "점수 자체는 21개 전체에서 나온 값 그대로다." if partial else "")
    st.markdown(
        "<div class='sc-muted'>위에서부터 순위가 높다. 막대가 왼쪽이면 그날 섹터들의 "
        f"가운데보다 낮았다는 뜻이고, 앞으로 오른다·내린다는 뜻이 아니다.{tail}</div>",
        unsafe_allow_html=True)


def _render_consensus(frame, names) -> None:
    """🔒 가중치에 기대지 않는 신호. 셋 다 상위 5 안이면 배지를 준다.

    🔒 **필터와 무관하고 슬라이더와도 무관하다** — 프리셋 셋을 고정으로 본다.
       그것이 이 칸의 요점이다.
    """
    latest = view.latest_frame(frame).set_index("sector_id")
    # 🔴 **세기 전에 결측을 버린다.** `nsmallest` 는 순위가 있는 섹터가 5개보다 적으면
    #    모자란 만큼 `pd.NA` 행으로 채워 준다(pandas 3.0.5 실측). 그 `pd.NA` 가 아래
    #    정렬 키로 들어가 `boolean value of NA is ambiguous` 로 페이지를 죽였다 (이슈 #3).
    top = {p: set(latest[latest[f"rank_{p}"].notna()].nsmallest(5, f"rank_{p}").index)
           for p in view.PROFILES}
    # 🔒 정렬 키에 결측이 들어올 수 없다 — 위 `notna()` 가 거른 집합의 교집합이기
    #    때문이다. 🔴 여기에 "결측이면 맨 뒤" 같은 가지를 **더 두지 않는다**: 닿지
    #    않는 가지는 테스트가 지킬 수 없고, 그러면 위 필터를 지워도 아무도 모른다
    consensus = sorted(set.intersection(*top.values()),
                       key=lambda sid: latest.loc[sid, "rank_balanced"])
    if not consensus:
        st.markdown(
            "<div class='sc-muted'>세 가중치 모두에서 상위 5에 든 섹터가 없다. "
            "즉 지금 순위는 가중치를 무엇으로 두느냐에 민감하다.</div>",
            unsafe_allow_html=True)
        return
    for sid in consensus:
        row = latest.loc[sid]
        ranks = " · ".join(f"{p} {rank_badge(view.int_or_none(row[f'rank_{p}']))}"
                           for p in view.PROFILES)
        st.markdown(f"- **{names.sector_label(sid)}** — {ranks}")
    st.markdown("<div class='sc-muted'>프리셋 셋을 고정으로 본다 — 슬라이더와 필터를 "
                "따르지 않는다.</div>", unsafe_allow_html=True)


def _render_gics_distribution(frame, names, keep: frozenset[str]) -> None:
    """대분류별 순위 분포. 🔒 **점수가 아니다** — 목록이 곧 분포다 (`view.gics_distribution`).

    🔒 필터를 따른다 — 표·등수·막대와 **같은 규칙**이다. 한 화면에서 칸마다 규칙이
       다르면 캡션으로 설명될 차이가 아니다. (`_render_consensus` 만 예외이고 그
       이유는 그 함수의 머리주석에 있다.)
    """
    table = view.gics_distribution(frame, names=names, only=keep)
    if len(table) == 0:
        st.markdown("<div class='sc-muted'>보일 대분류가 없다.</div>",
                    unsafe_allow_html=True)
        return
    st.dataframe(
        table,
        width="stretch",
        column_config={
            "테마수": st.column_config.NumberColumn(
                "테마수", help="이 대분류에 묶인 테마 섹터가 몇 개인가", format="%d"),
            "최고순위": st.column_config.NumberColumn(
                "최고순위", help="🔒 정렬은 이 열로 한다. 그 대분류에서 가장 높은 자리다",
                format="%d"),
            "순위": st.column_config.TextColumn(
                "순위 분포", help="🔒 표시 전용이다 — 이 열로 다시 정렬하면 사전순이 되어 "
                                "10 이 2 앞에 온다"),
            "순위없음": st.column_config.NumberColumn(
                "순위없음", help="창이 안 차 점수를 낼 수 없었던 섹터 수. 0 이 아니라 **없음**이다",
                format="%d"),
        },
    )
    st.markdown(
        "<div class='sc-muted'>🔒 <b>대분류 점수가 아니다.</b> 대분류 ETF 는 살 수 없어 "
        "매매 단위가 아니고, 그래서 평균도 중위도 내지 않는다 — 목록이 곧 분포다. "
        "숫자는 언제나 그날 21개 테마 안에서의 자리이고, 가중치를 따르며 필터가 숨긴 "
        "섹터는 빠져 있다.</div>",
        unsafe_allow_html=True)


def _render_breakdown(frame, sector_id: str, profile: str, names) -> None:
    with theme.panel(names.sector_full(sector_id)):
        # ★ 질문칸은 여기와 조 페이지에만 연다 — 확정 모달에는 두지 않는다 (ADR-SC-0013)
        evidence.render_evidence(frame, sector_id, profile=profile, names=names,
                                 days=_STABILITY_DAYS, ask_key="rank_ask")


def _floor(column) -> int:
    """막대 눈금의 아래끝. 🔒 0 으로 고정하지 않는다 — 음수 점수가 잘린다.

    🔴 **결측을 먼저 버린다.** 최신일이 전부 결측이면 `column.min()` 이 `pd.NA` 이고,
       `min(pd.NA, 0)` 은 `boolean value of NA is ambiguous` 로 표를 못 그리게 한다
       (이슈 #3). 그때 눈금은 `0~1` 이고 막대는 전부 비어 그려진다 — 값이 없다는
       사실이 화면에 그대로 남는다.
    """
    values = column.dropna()
    return int(min(values.min(), 0)) if len(values) else 0


def _ceil(column) -> int:
    values = column.dropna()
    return int(max(values.max(), 1)) if len(values) else 1
