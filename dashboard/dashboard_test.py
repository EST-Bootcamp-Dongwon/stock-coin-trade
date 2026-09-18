"""화면 계층의 계약 — 뷰 모델(순수)과 실제 렌더(`AppTest`).

🔴 **Playwright 는 이 환경에 없다**(MCP 로도 로컬에도). 대신 Streamlit 이 주는
   `AppTest` 로 헤드리스 렌더를 본다. 실제로 이것이 배포 전에 앱을 못 뜨게 하는
   버그를 잡았다 — 네 페이지의 callable 이 전부 `render` 라 `st.navigation` 의
   URL 경로가 충돌했다. 순수 함수 테스트로는 잡히지 않는 종류다.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

#: 🔒 저장소 루트를 **파일 위치에서** 끌어낸다. `Path("streamlit_app.py")` 는 pytest 를
#:    어디서 부르느냐에 따라 어긋난다 (실제로 FileNotFoundError 로 터졌다).
ROOT = Path(__file__).resolve().parent.parent

from dashboard import explain, view, weights
from sector.scoring import (AXES, AXIS_NAMES, PRESETS, rank_scores, scoring_axes,
                            weighted_score_bp)

# ── 뷰 모델 (화면 없이) ─────────────────────────────────────────────────────

DAYS = [f"2026090{i}" for i in range(1, 10)]


#: 🔴 **축마다 z 가 달라야 한다.** 네 축을 같은 값으로 두면 어떤 가중치로도 같은 점수가
#:    나와, "프리셋은 저장 열을 읽는다"(ADR-SC-0014 ③) 같은 계약이 픽스처 위에서 **참으로
#:    고정되지 않는다.** 적대적 리뷰가 돌연변이로 그것을 보였다 — `scored()` 의 프리셋
#:    분기를 통째로 지워도 테스트가 한 건도 안 깨졌다.
#: 🔒 100 의 배수로 둔다. `w·z/100` 이 정수로 떨어져 기여의 합이 총점과 **정확히** 같아진다
#:    (`test_기여도_합이_점수와_같다` 가 그것을 고정한다).
_AXIS_OFFSET = {"m": 0, "f": 1200, "b": -800, "v": 300}


def frame(n_sectors: int = 3, *, diverging: bool = False) -> pd.DataFrame:
    """합성 점수 표. 🔒 실제 KRX 데이터를 픽스처로 쓰지 않는다 (AGENTS.md 5장).

    🔒 저장 열(`score_*_bp`·`rank_*`)을 **배치가 쓰는 함수로** 채운다 — 즉 이 픽스처는
       게시 게이트를 통과하는 **유효한 게시본**이다. 손으로 적으면 픽스처가 스스로
       모순되고, 그 위에서 고정한 계약은 아무것도 보장하지 않는다.

    ## `diverging=True` — **축마다 순위가 갈린다**

    🔴 기본 픽스처는 `base = (s-1)*5000` 에 축 오프셋만 더해서, 네 축이 섹터를
       **똑같은 순서로** 세운다. 그래서 어떤 가중치를 주어도 순위가 한 칸도 안 바뀌고,
       "화면의 이 칸이 가중치를 따르는가" 같은 계약을 **픽스처 위에서 고정할 수 없다**
       (2026-09-18 실측 — 분포표를 프리셋으로 못 박는 돌연변이가 살아남았다).

    `diverging=True` 는 M 축과 V 축의 순서를 **뒤집어** 둔다. 그러면 균형 프리셋과
    V 축만 쓰는 가중치가 서로 다른 1위를 낸다.
    """
    rows = []
    for day_i, day in enumerate(DAYS):
        z_of = {}
        for s in range(n_sectors):
            base = (s - 1) * 5000 + day_i * 100
            z_of[f"sec_{s}"] = {a.upper(): base + off for a, off in _AXIS_OFFSET.items()}
            if diverging:
                # 🔒 V 축만 뒤집는다 — M·F·B 는 그대로라 균형은 여전히 sec_2 를 위로 본다
                z_of[f"sec_{s}"]["V"] = (n_sectors - 2 - s) * 5000 + day_i * 100
        scores = {name: {sid: weighted_score_bp(z, w) for sid, z in z_of.items()}
                  for name, w in PRESETS.items()}
        ranks = {name: rank_scores(column) for name, column in scores.items()}
        for s in range(n_sectors):
            sid = f"sec_{s}"
            z = z_of[sid]
            rows.append({
                "bas_dd": day, "sector_id": sid, "gics": "Industrials",
                "m_raw_bp": 100 + s, "f_raw_bp": 200 + s,
                "b_raw_bp": 300 + s, "v_raw_bp": 400 + s,
                "m_z_bp": z["M"], "f_z_bp": z["F"], "b_z_bp": z["B"], "v_z_bp": z["V"],
                "n_axes_used": 4, "axes_missing": "", "axes_degraded": "",
                **{f"score_{name}_bp": scores[name][sid] for name in PRESETS},
                **{f"rank_{name}": ranks[name][sid] for name in PRESETS},
                "liquidity_ok": True, "etf_n": 2, "is_partial": False,
                "config_version": "t", "config_sha256": "x", "fetched_at": "t",
            })
    return pd.DataFrame(rows)


#: 테스트가 기본으로 쓰는 가중치. 🔒 프리셋이라 `scored()` 가 저장 열을 그대로 읽는다
BALANCED = weights.Weighting.preset("balanced")


def scored(n_sectors: int = 3, weighting: weights.Weighting = BALANCED) -> pd.DataFrame:
    """`score_bp` · `rank` 가 붙은 프레임 — 표·막대·등수가 읽는 모양."""
    return view.scored(frame(n_sectors), weighting)


#: 결측으로 만들 열. 🔒 **z 와 원시값까지** 비운다 — 점수만 비우면 축 분해가 값을 갖고
#:    있어 "점수는 없는데 근거는 있다" 는, 실제로는 나올 수 없는 모양이 된다
_BLANK_COLUMNS = (["m_z_bp", "f_z_bp", "b_z_bp", "v_z_bp",
                   "m_raw_bp", "f_raw_bp", "b_raw_bp", "v_raw_bp"]
                  + [f"score_{p}_bp" for p in PRESETS] + [f"rank_{p}" for p in PRESETS])


def blank_latest(n_sectors: int = 3, *, blanks: int | None = None) -> pd.DataFrame:
    """최신일의 점수·순위가 **결측인** 프레임. `blanks=None` 이면 전부, 정수면 그만큼.

    🔴 `sectors.yaml` 에 섹터를 하나 더 넣으면 **그날부터 이 모양이다.** 새 섹터는
       20영업일 창이 찰 때까지 z 를 못 내고, z 가 없으면 점수도 순위도 없다
       (`weighted_score_bp` 가 분모 0 에서 `None`). 실제 파생본도 5985행 중 **399행**이
       이 모양이다 — 창이 안 찬 초기 19영업일.

    🔒 dtype 을 `Int64`·`boolean` 으로 맞춘다. `float64` 로 눕히면 결측이 `nan` 이 되어
       **재현하려는 `pd.NA` 경로를 안 밟는다.** 실제 파생본은 정수 열이 전부 nullable 이다.
    """
    out = frame(n_sectors)
    for column in _BLANK_COLUMNS + ["n_axes_used", "etf_n"]:
        out[column] = out[column].astype("Int64")
    out["liquidity_ok"] = out["liquidity_ok"].astype("boolean")

    last = out["bas_dd"] == out["bas_dd"].max()
    ids = sorted(out.loc[last, "sector_id"].unique())
    mask = last & out["sector_id"].isin(ids if blanks is None else ids[:blanks])
    for column in _BLANK_COLUMNS:
        out.loc[mask, column] = pd.NA
    out.loc[mask, "n_axes_used"] = 0
    out.loc[mask, "axes_missing"] = ",".join(AXES)
    out.loc[mask, "is_partial"] = True
    # 🔒 유동성도 **판정 불가**다 — 창이 안 찼으면 거래대금 평균도 못 낸다
    out.loc[mask, "liquidity_ok"] = pd.NA
    return out


def test_마지막_날만_고른다():
    assert set(view.latest_frame(frame())["bas_dd"]) == {DAYS[-1]}


def test_요청한_일수가_없으면_있는_만큼을_말한다():
    """🔴 9일치뿐인데 "최근 20영업일" 이라 이름 붙이면 그것이 거짓말이다.

    조용히 줄이는 대신 `stability_window` 가 **실제 일수**를 돌려주고 화면이
    그 숫자로 이름을 붙인다.
    """
    data = frame()
    assert view.stability_window(data, days=20) == len(DAYS)
    assert view.stability_window(data, days=3) == 3

    stability = view.rank_stability(view.scored(data, BALANCED), days=20)
    assert (stability["rank_days"] == len(DAYS)).all()       # 표본일이 사실을 말한다


def test_이력이_짧은_섹터는_평균을_내지_않는다():
    """🔒 섹터마다 이력이 다를 수 있다. 짧은 쪽을 긴 척하지 않는다."""
    data = frame()
    data = data[~((data["sector_id"] == "sec_0") & (data["bas_dd"] == DAYS[0]))]
    stability = view.rank_stability(view.scored(data, BALANCED), days=len(DAYS))
    assert pd.isna(stability.loc["sec_0", "rank_mean"])      # 하루 모자라 비워 둔다
    assert stability.loc["sec_1", "rank_mean"] == stability.loc["sec_1", "rank_mean"]


def test_기여도_합이_점수와_같다():
    """🔴 축 분해가 게시된 점수와 맞물린다 — '왜 1위인가' 의 근거가 흔들리지 않는다."""
    data = frame()
    for sector_id in data["sector_id"].unique():
        parts = view.axis_breakdown(data, sector_id, weights=PRESETS["balanced"])
        total = sum(p["contribution_bp"] or 0 for p in parts)
        score = int(view.latest_frame(data).set_index("sector_id")
                    .loc[sector_id, "score_balanced_bp"])
        assert abs(total - score) <= 1, sector_id        # bp 반올림 1 까지 허용


def test_결측축은_기여가_0이_아니라_없음이다():
    """🔒 0 은 '중립적으로 기여했다' 는 뜻이 된다. 없는 것은 없다 (ADR-SC-0007)."""
    data = frame()
    data.loc[data["sector_id"] == "sec_0", "f_z_bp"] = None
    parts = {p["axis"]: p for p in view.axis_breakdown(data, "sec_0", weights=PRESETS["balanced"])}
    assert parts["F"]["contribution_bp"] is None
    assert parts["F"]["z_bp"] is None


def test_결측축이_있으면_나머지_가중치가_다시_나뉜다():
    data = frame()
    data.loc[data["sector_id"] == "sec_0", "f_z_bp"] = None
    parts = [p for p in view.axis_breakdown(data, "sec_0", weights=PRESETS["balanced"])
             if p["contribution_bp"] is not None]
    live_weight = sum(PRESETS["balanced"][p["axis"]] for p in parts)
    assert live_weight == 100 - PRESETS["balanced"]["F"]


def test_없는_섹터는_빈_목록이다():
    assert view.axis_breakdown(frame(), "없는섹터", weights=PRESETS["balanced"]) == []


@pytest.mark.parametrize("profile", list(PRESETS))
def test_프리셋마다_표가_나온다(profile):
    table = view.ranking_table(scored(weighting=weights.Weighting.preset(profile)))
    assert len(table) == 3 and "순위" in table.columns


# ── 등수 · 막대 · 서술의 재료 ───────────────────────────────────────────────
# 🔴 M8 직후 `narrative()` 와 `Names` 는 **정의만 되고 아무도 부르지 않았다.**
#    화면은 `steel` 을 그렸고 서술은 죽은 코드였다. 아래가 그 회귀를 막는다.

KOREAN = view.Names(sector={"sec_0": "가", "sec_1": "나", "sec_2": "철강"},
                    gics={"Industrials": "산업재"})


def test_랭킹표에_한국어_이름이_들어간다():
    """🔴 `names=` 를 빠뜨리면 표가 조용히 코드를 그린다 — 실제로 그랬다."""
    table = view.ranking_table(scored(), names=KOREAN)
    assert list(table["섹터"]) == [KOREAN.sector_label(s) for s in table.index]
    assert "철강" in set(table["섹터"])
    assert set(table["GICS"]) == {"산업재"}


def test_이름을_모르면_코드를_그대로_준다():
    """🔒 지어내지 않는다. `sectors.yaml` 을 못 읽어도 화면은 산다."""
    table = view.ranking_table(scored(), names=view.Names.empty())
    assert list(table["섹터"]) == list(table.index)


def test_등수_카드는_순위_순서로_상위만_준다():
    entries = view.podium(scored(), weighting=BALANCED, top=2, names=KOREAN)
    assert [e["rank"] for e in entries] == [1, 2]
    assert entries[0]["label"] == "철강"          # sec_2 가 1위다
    assert all(e["sector_id"] in KOREAN.sector for e in entries)


def test_끌어올린_축이_없으면_지어내지_않는다():
    """🔒 기여가 전부 음수면 `lead_axis` 는 `None` 이고 문구가 그 사실을 말한다."""
    data = frame()
    for column in ("m_z_bp", "f_z_bp", "b_z_bp", "v_z_bp"):
        data[column] = -5000
    entry = view.podium(view.scored(data, BALANCED), weighting=BALANCED, top=1)[0]
    assert entry["lead_axis"] is None
    assert "지어" not in explain.lead_axis_text(None)      # 문구가 존재한다
    assert "없다" in explain.lead_axis_text(None)


def test_끌어올린_축은_기여가_가장_큰_축이다():
    data = frame()
    data["f_z_bp"] = 29000                       # 자금흐름만 크게 띄운다
    assert view.podium(view.scored(data, BALANCED), weighting=BALANCED,
                       top=1)[0]["lead_axis"] == "F"


def test_막대는_순위_순서와_σ_로_준다():
    """🔒 화면이 `sort=False` 로 그리므로 이 순서가 곧 화면 순서다."""
    bars = view.score_bars(scored(), names=KOREAN)
    assert list(bars.index) == ["철강", "나", "가"]          # 1위부터
    assert bars["점수(σ)"].iloc[0] == pytest.approx(
        view.latest_frame(frame()).set_index("sector_id")
        .loc["sec_2", "score_balanced_bp"] / 10000)


def test_서술_재료는_없는_섹터에_빈_것을_준다():
    assert view.sector_story(frame(), "없는섹터") == {}


def test_서술_재료가_서술_함수에_그대로_들어간다():
    """🔴 두 쪽이 어긋나면 화면이 `TypeError` 로 죽는다. 계약을 여기서 고정한다."""
    story = view.sector_story(frame(), "sec_2", names=KOREAN)
    lines = explain.narrative(**story)
    assert lines and "철강" in lines[0] and "1위" in lines[0]
    assert "투자" not in lines[0]
    assert "앞으로 오른다는 뜻이 아니" in lines[-1]     # 🔒 마지막 줄은 언제나 이것이다


def test_산수표의_기여합이_총점과_같다():
    """🔴 읽는법 예시의 요점이 '덧셈이 맞아떨어진다' 는 것이다."""
    data = frame()
    table = view.arithmetic_table(data, "sec_2")
    latest = view.latest_frame(data).set_index("sector_id")
    assert table["④ 기여(bp)"].sum() == latest.loc["sec_2", "score_balanced_bp"]


def test_조사가_붙는다():
    """🔒 '철강은' · '가는' — 기계가 쓴 티가 나는 `은(는)` 을 쓰지 않는다."""
    assert explain.josa("철강", "은는") == "은"
    assert explain.josa("가", "은는") == "는"
    assert explain.josa("철강 (steel)", "이가") == "이"


# ── 문구 ────────────────────────────────────────────────────────────────────

def test_값이_없으면_0_이_아니라_대시다():
    assert explain.axis_raw_text("M", None) == "—"
    assert "—" in explain.axis_line("M", raw_bp=None, z_bp=None, rank=None, total=21)
    assert "—" in explain.liquidity_text(None)


def test_모든_축에_뜻과_반례가_있다():
    """🔴 뜻만 적으면 오독이 줄지 않는다. '무엇이 아닌가' 를 함께 둔다."""
    for axis in AXES:
        assert explain.AXIS_MEANING[axis] and explain.AXIS_NOT[axis]
        assert explain.AXIS_UNIT[axis]


def test_유동성은_모름과_미달을_가른다():
    assert explain.liquidity_text(None) != explain.liquidity_text(False)


def test_프리셋_문구가_가중치를_숨기지_않는다():
    """순위가 왜 다른지의 답이 가중치다."""
    for profile, weights in PRESETS.items():
        label = explain.preset_label(profile)
        assert all(str(w) in label for w in weights.values())


# ── 실제 렌더 ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=120)
    at.run()
    return at


def test_앱이_예외_없이_뜬다(app):
    """🔴 네 페이지가 모두 `render` 라 URL 이 충돌해 앱이 죽었던 자리다."""
    assert not app.exception, [str(e)[:200] for e in app.exception]


def test_면책이_머리글에_있다(app):
    assert any("투자 권유가 아니다" in w.value for w in app.warning)


def test_출처가_푸터에_있다(app):
    """약관이 정한 의무 문자열. 🔒 글자를 바꾸지 않는다."""
    assert any("한국거래소 통계정보" in c.value for c in app.caption)


def test_어느_데이터를_읽었는지_화면이_말한다(app):
    assert any("이 화면이 읽은 것" in c.value for c in app.caption)


def test_프리셋_셋을_동시에_준다(app):
    assert app.radio and len(app.radio[0].options) == len(PRESETS)


def test_세_페이지가_있고_확정_페이지는_없다():
    """🔒 확정은 랭킹의 모달이다 (ADR-SC-0012 ②). 페이지가 되살아나면 근거가 두 곳으로 갈린다."""
    import importlib.util

    from dashboard.pages import howto, ranking, teams

    assert all(hasattr(m, "render") for m in (ranking, teams, howto))
    assert importlib.util.find_spec("dashboard.pages.confirm") is None


def test_페이지마다_URL_경로가_다르다():
    """🔒 이것이 앱을 못 뜨게 했던 버그다. 경로를 명시했는지 파일에서 확인한다."""
    source = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    paths = [line.split('url_path="')[1].split('"')[0]
             for line in source.splitlines() if "url_path=" in line and "st.Page" in line]
    assert len(paths) == 3 and len(set(paths)) == 3, paths


def _render_sources():
    """렌더 경로의 소스만. 🔒 **테스트 파일 자신은 뺀다** — 금지 문자열을 검사
    코드가 담고 있어 자기 자신에 걸린다."""
    return [p for p in (ROOT / "dashboard").rglob("*.py") if not p.name.endswith("_test.py")]


def test_다운로드_버튼이_없다():
    """🔴 public 앱이다. 화면 표출은 약관 허용 범위지만 **파일 제공은 아니다**."""
    for path in _render_sources():
        assert "download_button" not in path.read_text(encoding="utf-8"), path


def test_토큰_값이_화면에_그려지지_않는다(app):
    """🔴 **렌더된 실제 문자열**을 훑는다.

    앞서 이 테스트는 `HF_TOKEN_READ` 라는 **이름**을 금지했는데, 그 이름은
    "무엇을 설정하라" 고 알려주는 안내 문구에 나온다 — 이름을 말하는 것은 유출이
    아니라 친절이다(`hub._token` 이 이미 그렇게 한다). 유출은 **값**이 나가는 것이다.
    그래서 계약이 아니라 결과를 본다.
    """
    from sector.secret_access import get_secret

    secrets = [get_secret(name, required=False)
               for name in ("HF_TOKEN_READ", "HF_TOKEN_WRITE", "KRX_API_KEY")]
    secrets = [s for s in secrets if s and len(s) >= 12]
    if not secrets:
        pytest.skip("이 환경에 시크릿이 없어 확인할 수 없다")

    rendered = "\n".join(
        str(element.value) for group in (app.markdown, app.caption, app.warning,
                                         app.error, app.info, app.title, app.subheader)
        for element in group
    )
    for secret in secrets:
        assert secret not in rendered, "토큰 값이 화면에 그려졌다"


def test_시크릿을_st_에_직접_넘기는_줄이_없다():
    """정적 보강 — `st.write(hub.read_token())` 같은 줄을 금지한다."""
    import re

    pattern = re.compile(r"st\.\w+\([^)]*(read_token|write_token|get_secret)\s*\(")
    for path in _render_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not pattern.search(line), f"{path}:{number} — {line.strip()}"


# ── 쓰기 흐름 — 조 만들기 → 참가 → 확정 ─────────────────────────────────────
# 🔴 여기가 `AppTest` 의 진짜 값어치다. 렌더만 보면 "표가 나온다" 까지밖에 못 본다.
#    사람이 눌러야만 드러나는 것 — 폼 제출, 세션 상태, 원장 쓰기 — 을 자동으로 밟는다.


def _teams_page() -> None:
    from dashboard.pages import teams

    teams.render()


def _ranking_page() -> None:
    from dashboard.pages import ranking

    ranking.render()


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """원장을 로컬 임시 폴더로 돌린다. 🔒 테스트가 HF 를 부르지 않는다."""
    from dashboard import data
    from sector.workspace.store import LocalStore

    store = LocalStore(tmp_path)
    monkeypatch.setattr(
        data, "workspace_store",
        lambda: (store, data.Source("local", "테스트 원장")))
    return store


# ── 원장을 어디에 붙이는가 ──────────────────────────────────────────────────
# 🔴 여기서 조용히 폴백하면 팀이 **서로 다른 원장**에 쓰면서 같은 것을 본다고
#    믿게 된다. 못 붙는 것과 잘못 붙는 것을 다르게 다룬다 (`data.workspace_store`).


def _supabase_secrets(monkeypatch, **values):
    """`get_secret` 을 **두 자리에서** 가로챈다.

    🔒 `store.py` 는 `from … import get_secret` 로 **이름을 묶어 뒀다.** 모듈
       하나만 patch 하면 다른 쪽이 진짜 `.env` 를 읽어 테스트가 그 컴퓨터의 설정에
       따라 달라진다.
    """
    from sector import secret_access
    from sector.workspace import store as store_mod

    def fake(name, **_kw):
        return values.get(name)

    monkeypatch.setattr(secret_access, "get_secret", fake)
    monkeypatch.setattr(store_mod, "get_secret", fake)


def test_supabase_시크릿이_있으면_그리로_붙는다(monkeypatch):
    from dashboard import data

    _supabase_secrets(monkeypatch,
                      SUPABASE_URL="https://example.supabase.co",
                      SUPABASE_ANON_KEY="anon-테스트-키")
    store, source = data.workspace_store()
    assert source.kind == "supabase"
    assert source.detail == "example.supabase.co"       # 🔒 키는 어디에도 안 나온다
    assert not source.is_local
    assert store.url == "https://example.supabase.co"


def test_시크릿이_반쪽만_있으면_던진다(monkeypatch):
    """🔴 조용히 HF 로 내려가지 않는다 — 반쯤 옮긴 상태가 가장 위험하다."""
    from dashboard import data
    from sector.workspace import store as store_mod

    _supabase_secrets(monkeypatch, SUPABASE_URL="https://example.supabase.co")
    with pytest.raises(store_mod.StoreError, match="anon"):
        data.workspace_store()


def test_service_role_키를_넣으면_던진다(monkeypatch):
    """🔴 앱 칸에 secret 키를 붙여넣은 사고가 **침묵에 묻히면** 안 된다."""
    from dashboard import data
    from sector.workspace import store as store_mod

    _supabase_secrets(monkeypatch,
                      SUPABASE_URL="https://example.supabase.co",
                      SUPABASE_ANON_KEY="sb_secret_abcdef")
    with pytest.raises(store_mod.StoreError, match="secret 키"):
        data.workspace_store()


def _run(page, ledger):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_function(page, default_timeout=120)
    at.run()
    return at


def _make_team(at, *, actor="동원", team_id="team_a", name="A조", passcode="산-바다-강-들"):
    """🔴 위젯을 **key 로** 찾는다.

    처음엔 위치 인덱스(`at.text_input[1]`)를 썼는데, 마스터 게이트가 입력칸을
    하나 더 만들자 전부 한 칸씩 밀려 엉뚱한 곳에 값이 들어갔다. 화면이 바뀔 때마다
    깨지는 테스트는 신호가 아니라 잡음이다.
    """
    at.text_input(key="identity_name").input(actor).run()
    at.text_input(key="create_team_id").input(team_id)
    at.text_input(key="create_name").input(name)
    at.text_input(key="create_passcode").input(passcode)
    at.button(key="FormSubmitter:create-만들기").click().run()
    return at


def _open_as(page, ledger, *, actor="동원", team_id="team_a"):
    """이미 있는 조의 **조원으로** 페이지를 연다."""
    at = _run(page, ledger)
    at.session_state["sc_actor"] = actor
    at.session_state["sc_team_id"] = team_id
    return at.run()


def _as_member(page, ledger, *, actor="동원", team_id="team_a"):
    """조를 만들고 그 조원으로 페이지를 연다."""
    _make_team(_run(_teams_page, ledger), actor=actor, team_id=team_id)
    return _open_as(page, ledger, actor=actor, team_id=team_id)


def _confirm(at, reason: str):
    """🔒 모달은 플래그로 열린다(`team_actions` 머리주석) — 그래서 AppTest 가 끝까지 밟는다."""
    at.button(key="open_confirm").click().run()
    at.text_area(key="confirm_reason").input(reason).run()
    at.button(key="confirm_submit").click().run()
    return at


def _keys(elements) -> list[str]:
    return [element.key for element in elements]


def test_조를_만들면_원장에_남는다(ledger):
    at = _make_team(_run(_teams_page, ledger))
    assert not at.exception, [str(e)[:200] for e in at.exception]

    from sector.workspace import fold

    workspace = fold.fold(ledger.read_all())
    team = workspace.team("team_a")
    assert team is not None and team.name == "A조"
    assert team.created_by == "동원" and team.members == ("동원",)
    assert not team.is_confirmed          # 🔒 만들자마자 확정되지 않는다


def test_평문_passcode_가_원장_파일에_없다(ledger, tmp_path):
    """🔒 계약이 아니라 **디스크에 쓰인 바이트**를 본다."""
    secret = "산-바다-강-들-숲"
    _make_team(_run(_teams_page, ledger), passcode=secret)
    written = "\n".join(p.read_text(encoding="utf-8")
                        for p in (tmp_path / "events").glob("*.json"))
    assert written and secret not in written


def test_이름_없이는_조를_못_만든다(ledger):
    at = _run(_teams_page, ledger)
    at.text_input(key="create_team_id").input("team_a")
    at.text_input(key="create_name").input("A조")
    at.text_input(key="create_passcode").input("산-바다-강-들")
    at.button(key="FormSubmitter:create-만들기").click().run()
    assert any("이름" in e.value for e in at.error)
    assert ledger.read_all().events == ()        # 🔒 한 건도 쓰이지 않는다


def test_짧은_passcode_는_거부되고_원장이_비어_있다(ledger):
    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("동원").run()
    at.text_input(key="create_team_id").input("team_a")
    at.text_input(key="create_name").input("A조")
    at.text_input(key="create_passcode").input("1234")
    at.button(key="FormSubmitter:create-만들기").click().run()
    assert any("짧다" in e.value for e in at.error)
    assert ledger.read_all().events == ()


def test_조에_참가하지_않으면_랭킹에_확정_버튼이_없다(ledger):
    at = _run(_ranking_page, ledger)
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert "open_confirm" not in _keys(at.button)
    assert any("참가" in info.value for info in at.info)


def test_확정은_사유가_없으면_막히고_모달이_남는다(ledger):
    """🔴 사유 없는 확정은 3개월 뒤에 아무것도 남기지 않는다."""
    at = _as_member(_ranking_page, ledger)
    at.button(key="open_confirm").click().run()
    at.button(key="confirm_submit").click().run()      # 사유를 비운 채
    assert any("비어" in e.value for e in at.error)
    assert "confirm_reason" in _keys(at.text_area)      # 🔒 쓰던 것을 잃지 않는다

    from sector.workspace import fold
    assert not fold.fold(ledger.read_all()).team("team_a").is_confirmed


def test_랭킹에서_모달로_확정하면_원장에_남고_모달이_닫힌다(ledger):
    at = _as_member(_ranking_page, ledger)
    chosen = at.selectbox(key="rank_detail").value
    _confirm(at, "자금흐름 축이 3σ 로 압도적이다")
    assert not at.exception, [str(e)[:200] for e in at.exception]

    from sector.workspace import fold
    team = fold.fold(ledger.read_all()).team("team_a")
    assert team.is_confirmed and team.core_sector == chosen
    assert team.core_reason == "자금흐름 축이 3σ 로 압도적이다"
    assert team.confirmed_by == "동원"
    # 🔒 모달이 닫힌 뒤에도 무엇이 됐는지 말한다 — 모달 안의 success 는 다시 돌면 사라진다
    assert "confirm_reason" not in _keys(at.text_area)
    assert any("확정했다" in s.value for s in at.success)


def test_틀린_passcode_로는_남의_조에_못_들어간다(ledger):
    """🔴 passcode 가 지키는 것은 **쓰기 권한**이다. 이 경로가 뚫리면 의미가 없다."""
    _make_team(_run(_teams_page, ledger), actor="동원", passcode="산-바다-강-들")

    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("낯선사람").run()
    at.text_input(key="join_passcode").input("틀린-passcode-값")
    at.button(key="FormSubmitter:join-참가").click().run()

    assert any("passcode" in e.value for e in at.error)
    # 🔒 AppTest 의 session_state 는 `.get()` 을 키 조회로 해석한다 — 멤버십으로 본다
    assert "sc_team_id" not in at.session_state

    from sector.workspace import fold
    assert fold.fold(ledger.read_all()).team("team_a").members == ("동원",)


def test_맞는_passcode_면_참가하고_원장에_남는다(ledger):
    _make_team(_run(_teams_page, ledger), actor="동원", passcode="산-바다-강-들")

    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("민수").run()
    at.text_input(key="join_passcode").input("산-바다-강-들")
    at.button(key="FormSubmitter:join-참가").click().run()

    assert not at.exception, [str(e)[:200] for e in at.exception]
    from sector.workspace import fold
    assert fold.fold(ledger.read_all()).team("team_a").members == ("동원", "민수")


# ── 보관 · 되돌리기 ─────────────────────────────────────────────────────────
# 🔴 마스터(개발자)는 **없다** (2026-09-12 · ADR-SC-0011 ⑫ · V42). 보관은 만든
#    사람이 하고, 되돌리기는 그 조의 passcode 가 연다.

def test_만든_사람은_자기_조를_보관할_수_있다(ledger):
    at = _make_team(_run(_teams_page, ledger), actor="동원")
    # "참가 중" 패널 아래 보관 expander 의 사유 칸
    at.text_input(key="archive_reason_team_a").input("테스트 조였다").run()
    at.button(key="archive_team_a").click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]

    from sector.workspace import fold
    workspace = fold.fold(ledger.read_all())
    assert "team_a" not in workspace.active_teams
    assert workspace.team("team_a").archived_reason == "테스트 조였다"
    # 🔴 지운 것이 아니다 — 기록은 그대로다
    assert workspace.team("team_a").created_by == "동원"


def test_보관에_사유가_없으면_막힌다(ledger):
    at = _make_team(_run(_teams_page, ledger), actor="동원")
    at.button(key="archive_team_a").click().run()
    assert any("비어" in e.value for e in at.error)

    from sector.workspace import fold
    assert "team_a" in fold.fold(ledger.read_all()).active_teams


def test_남의_조는_보관_칸이_보이지_않는다(ledger):
    """🔒 만든 사람이 아니면 버튼 자체가 없다."""
    _make_team(_run(_teams_page, ledger), actor="동원", passcode="산-바다-강-들")

    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("민수").run()
    at.text_input(key="join_passcode").input("산-바다-강-들")
    at.button(key="FormSubmitter:join-참가").click().run()
    keys = [b.key for b in at.button]
    assert "archive_team_a" not in keys, keys


def _archive(at, *, reason="테스트 조였다", team_id="team_a"):
    at.text_input(key=f"archive_reason_{team_id}").input(reason).run()
    at.button(key=f"archive_{team_id}").click().run()
    return at


def test_보관된_조는_그_조_passcode_로_되돌린다(ledger):
    """🔴 V42 의 답이다 — 마스터가 아니라 **그 조의 passcode** 가 연다."""
    from sector.workspace import fold

    _archive(_make_team(_run(_teams_page, ledger), actor="동원", passcode="산-바다-강-들"))
    assert "team_a" not in fold.fold(ledger.read_all()).active_teams

    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("민수").run()
    at.text_input(key="restore_passcode_team_a").input("산-바다-강-들")
    at.button(key="FormSubmitter:restore_team_a-되돌리기").click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]

    workspace = fold.fold(ledger.read_all())
    assert "team_a" in workspace.active_teams
    # 🔒 참가와 같은 꼬리를 쓴다 — 되돌린 사람이 조원으로 남는다
    assert "민수" in workspace.team("team_a").members
    assert at.session_state["sc_team_id"] == "team_a"


def test_틀린_passcode_로는_되돌리지_못한다(ledger):
    from sector.workspace import fold

    _archive(_make_team(_run(_teams_page, ledger), actor="동원", passcode="산-바다-강-들"))

    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("민수").run()
    at.text_input(key="restore_passcode_team_a").input("산-바다-강-숲")
    at.button(key="FormSubmitter:restore_team_a-되돌리기").click().run()
    assert any("확인한다" in e.value for e in at.error)
    assert "team_a" not in fold.fold(ledger.read_all()).active_teams


def test_보관된_조가_없으면_칸이_없다(ledger):
    """🔒 평소에는 있을 일이 아니다 — 빈 칸을 그리지 않는다."""
    at = _make_team(_run(_teams_page, ledger))
    assert "restore_passcode_team_a" not in [t.key for t in at.text_input]


def test_보관된_조는_아무나_볼_수_있다(ledger):
    """🔒 원장 읽기는 이미 공개다(⑥). 감추면 자기 조를 아무도 못 찾는다.

    🔴 옛 화면은 이 칸을 **마스터에게만** 보여줬다. 마스터가 없어진 지금
       감춰 두면 보관된 조를 되돌릴 방법이 아무 데도 없다.
    """
    _archive(_make_team(_run(_teams_page, ledger), actor="동원"))
    at = _run(_teams_page, ledger)          # 🔒 이름도 안 적은 새 방문자다
    assert "restore_passcode_team_a" in [t.key for t in at.text_input]
    assert "사유 «테스트 조였다»" in _markdown(at)

# ── 근거를 두 화면이 같이 그린다 ────────────────────────────────────────────
# 🔴 M8 직후 확정 화면에는 점수 한 줄뿐이었다. 사유를 쓰라고 하면서 무엇을 근거로
#    쓸지는 안 보여주면, 사유 칸은 "1위라서" 로 채워진다.


def _markdown(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def test_확정_모달이_근거를_함께_보여준다(ledger):
    """🔴 모달이 랭킹을 가린다 — 사유를 쓰는 동안 근거가 안 보이면 사유 칸이 "1위라서" 로 채워진다.

    🔒 랭킹과 **같은 함수**를 부르므로 문구가 갈라지지 않는다.
    """
    at = _as_member(_ranking_page, ledger)
    before = _markdown(at).count("사람이 쓴 근거")
    at.button(key="open_confirm").click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]

    body = _markdown(at)
    assert body.count("사람이 쓴 근거") == before + 1   # 모달 안에 한 벌 더
    assert "앞으로 오른다는 뜻이 아니" in body


def test_확정한_뒤에는_조_페이지가_근거와_한국어_이름을_보여준다(ledger):
    """🔴 확정은 끝이 아니라 3개월 운용의 시작이다. 팀은 `steel` 이 아니라 `철강` 으로 말한다."""
    from dashboard import data as _data
    from dashboard import theme as _theme

    at = _as_member(_ranking_page, ledger)
    chosen = at.selectbox(key="rank_detail").value
    _confirm(at, "자금흐름이 압도적이다")

    at = _open_as(_teams_page, ledger)
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert any("이 섹터의 근거" in h.value for h in at.subheader)
    names = _data.sector_names()
    if not names.sector:
        pytest.skip("이 환경에서 `sectors.yaml` 을 읽을 수 없다")
    assert _theme.esc(names.sector_full(chosen)) in _markdown(at)


def test_이미_확정했으면_다른_섹터를_랭킹에서_확정하지_못한다(ledger):
    """🔒 바꾸려면 **되돌린 뒤** 확정한다 — 되돌린 사유가 '포폴 변경 사유' 가 된다."""
    at = _as_member(_ranking_page, ledger)
    _confirm(at, "첫 판단")
    at.selectbox(key="rank_detail").select_index(1).run()
    assert "open_confirm" not in _keys(at.button)
    assert "조 페이지에서 먼저 되돌린다" in _markdown(at)


def test_조_페이지에서_모달로_확정을_되돌린다(ledger):
    at = _as_member(_ranking_page, ledger)
    _confirm(at, "첫 판단")

    at = _open_as(_teams_page, ledger)
    at.button(key="open_unconfirm").click().run()
    at.text_area(key="cancel_reason").input("반도체 쏠림이 과했다").run()
    at.button(key="cancel_submit").click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]

    from sector.workspace import fold
    workspace = fold.fold(ledger.read_all())
    assert not workspace.team("team_a").is_confirmed
    kinds = [event.kind for event in workspace.history_of("team_a")]
    assert kinds[-2:] == ["sector.confirmed", "sector.unconfirmed"]    # 🔒 지우지 않았다
    assert any("되돌렸다" in s.value for s in at.success)


# ── 근거 첨부 (2026-09-14 · ADR-SC-0012) ────────────────────────────────────


def test_근거를_붙이면_본문과_굳힌_링크가_원장에_남고_화면에_나온다(ledger):
    at = _as_member(_ranking_page, ledger)
    chosen = at.selectbox(key="rank_detail").value
    at.button(key="open_attach").click().run()
    at.text_area(key="attach_body").input("공시 원문을 봤다").run()
    at.text_area(key="attach_links").input(
        "HTTPS://Dart.FSS.or.kr:443/dsaf001/main.do\n\nhttps://한국.kr/경로").run()
    at.button(key="attach_submit").click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]

    from sector.workspace import fold
    comment = fold.fold(ledger.read_all()).comments_of("team_a", sector_id=chosen)[0]
    assert comment.body == "공시 원문을 봤다"
    assert comment.links == ("https://dart.fss.or.kr/dsaf001/main.do",
                             "https://xn--3e0b707e.kr/%EA%B2%BD%EB%A1%9C")
    body = _markdown(at)
    assert 'href="https://dart.fss.or.kr/dsaf001/main.do"' in body
    assert 'rel="noopener noreferrer nofollow"' in body
    assert "attach_body" not in _keys(at.text_area)


def test_http_링크는_거부되고_원장에_남지_않는다(ledger):
    at = _as_member(_ranking_page, ledger)
    at.button(key="open_attach").click().run()
    at.text_area(key="attach_body").input("봐라").run()
    at.text_area(key="attach_links").input("http://dart.fss.or.kr/").run()
    at.button(key="attach_submit").click().run()

    assert any("https" in e.value for e in at.error)
    assert "attach_body" in _keys(at.text_area)          # 🔒 쓰던 것을 잃지 않는다
    from sector.workspace import fold
    assert fold.fold(ledger.read_all()).comments == ()


#: 🔴 적대적 입력. 줄바꿈은 `chr(10)` 으로 만든다 — 빈 줄 뒤가 HTML 블록 밖으로 새는지 본다
_HOSTILE = '<iframe srcdoc="<script>parent.x=1</script>"></iframe><img src=x onerror=alert(1)>'
_BREAKOUT = "첫 줄" + chr(10) * 2 + "![t](https://example.invalid/pixel.png) www.example.invalid"
#: 이스케이프돼도 모양이 남는 표지 — 이것이 보이는 요소는 사람 글을 품었다
_MARKS = ("iframe", "onerror", "example", "192.168")
#: 🔒 조 생성(지금)보다 늘 뒤다. 과거 시각을 쓰면 벽시계에 따라 "없는 조" 로 접혀 검사가 헛돈다
_FUTURE = "2099-01-01T00:0{}:00+00:00"


def _widget_texts(at) -> list[str]:
    """마크다운 요소가 아닌데 **마크다운으로 그려지는** 자리 — 오류 · 성공 · 안내 · 라벨."""
    groups = (at.error, at.success, at.warning, at.info, at.caption, at.subheader, at.title)
    texts = [str(element.value) for group in groups for element in group]
    return texts + [str(e.label) for e in at.expander] + [str(b.label) for b in at.button]


def test_사람이_쓴_글은_HTML_블록으로만_그려진다(ledger):
    """🔴 원장 읽기는 공개이고 RPC 는 앱의 쓰기 검사를 건너뛴다 — **그릴 때** 막아야 한다.

    Streamlit 1.63 은 `unsafe_allow_html` 을 정화하지 않고(Y3), 마크다운 역슬래시
    이스케이프는 GFM 자동 링크를 못 막는다(Y9 — remark-gfm 으로 재현). 그래서 사람 글이
    든 요소는 **전부 한 줄짜리 `<div>` HTML 블록**이어야 하고, 라벨 · 오류 · 성공 문구에는
    사람 글이 없어야 한다. 🔒 소스 문자열의 모양이 아니라 **어느 자리에 들어갔나**를 본다.
    """
    from sector.workspace import events

    _make_team(_run(_teams_page, ledger), actor="동원",
               name="<img src=x onerror=alert(1)>조 www.example.org")
    ledger.append([
        events.sector_confirmed(team_id="team_a", sector_id="steel", actor="동원",
                                reason=_HOSTILE + _BREAKOUT, at=_FUTURE.format(1)),
        events.comment_posted(team_id="team_a", body=_BREAKOUT, sector_id="steel",
                              actor="[민수](https://example.invalid/me)", at=_FUTURE.format(2)),
        # 앱을 거치지 않고 쓴 것 — 링크 · 섹터 id 검사를 지나지 않았다
        events.make_event("comment.posted", team_id="team_a", actor="http://192.168.0.1/me",
                          at=_FUTURE.format(3),
                          payload={"body": _HOSTILE, "sector_id": "steel",
                                   "links": ["http://192.168.0.1/admin"]}),
        events.make_event("sector.confirmed", team_id="team_a", actor="민수",
                          at=_FUTURE.format(4),
                          payload={"sector_id": "![x](https://example.invalid/x.png)",
                                   "reason": "우회"}),
    ])

    teams_at = _open_as(_teams_page, ledger)
    ranking_at = _open_as(_ranking_page, ledger)
    if ranking_at.selectbox(key="rank_detail").value != "steel":
        ranking_at.selectbox(key="rank_detail").select("steel").run()

    for at in (teams_at, ranking_at):
        assert not at.exception, [str(e)[:200] for e in at.exception]
        for text in _widget_texts(at):
            assert not any(mark in text for mark in _MARKS), text
        shown = [str(m.value) for m in at.markdown
                 if any(mark in str(m.value) for mark in _MARKS)]
        assert shown, "적대적 입력이 한 번도 안 나왔다 — 검사가 헛돈다"
        for value in shown:
            assert value.startswith("<div class='sc-"), value   # 🔒 HTML 블록 — 자동 링크를 안 받는다
            assert len(value.splitlines()) == 1, value          # 🔒 한 줄 — 블록 밖으로 못 나온다
            assert "<iframe" not in value and "<img" not in value, value
        body = _markdown(at)
        assert 'href="javascript' not in body and 'href="http://' not in body

    from sector.workspace import fold
    assert fold.fold(ledger.read_all()).team("team_a").core_sector == "steel"   # 우회 확정은 반영 안 됨


def test_읽지_못한_줄이_있어도_조_화면과_랭킹이_선다(ledger, tmp_path):
    """🔴 원장 한 줄이 **모든 조**의 화면을 멈췄다(2026-09-14 · ADR-SC-0011 ⑬).

    append-only 라 그 줄은 지울 수 없다 — 화면이 그 줄을 안고 서야 하고, 조 페이지는
    그 사실을 **HTML 블록으로** 말해야 한다(줄 안의 글도 원장에서 온 사람 글이다).
    """
    import json

    from sector.workspace import events

    _make_team(_run(_teams_page, ledger))
    bad = events.comment_posted(team_id="team_a", body="원래 본문", sector_id="steel",
                                actor="민수", at=_FUTURE.format(1)).to_json()
    bad["payload"]["body"] = "id 와 어긋난 본문"
    bad["event_id"] += "<img src=x onerror=alert(1)>"
    (tmp_path / "events" / f"{bad['event_id']}.json").write_text(
        json.dumps(bad, ensure_ascii=False), encoding="utf-8")

    teams_at = _open_as(_teams_page, ledger)
    ranking_at = _open_as(_ranking_page, ledger)
    for at in (teams_at, ranking_at):
        assert not at.exception, [str(e)[:200] for e in at.exception]
        assert not [e for e in at.error if "원장" in str(e.value)], [str(e.value) for e in at.error]

    assert any("원장에 어긋난 것 1건" in e.label for e in teams_at.expander)
    lines = [str(m.value) for m in teams_at.markdown if "읽지 못해" in str(m.value)]
    assert len(lines) == 1, lines
    assert lines[0].startswith("<div class='sc-") and "<img" not in lines[0], lines[0]
    assert any("우리 조" in str(s.value) for s in ranking_at.subheader)
    assert "open_confirm" in _keys(ranking_at.button)      # 🔒 쓰기 칸까지 선다


def test_다른_섹터를_고르면_열려_있던_모달이_닫힌다(ledger):
    """🔒 남은 플래그가 나중에 엉뚱한 섹터의 모달을 열지 않는다."""
    at = _as_member(_ranking_page, ledger)
    at.button(key="open_attach").click().run()
    assert "attach_body" in _keys(at.text_area)
    at.selectbox(key="rank_detail").select_index(1).run()
    assert "attach_body" not in _keys(at.text_area)
    assert "sc_dialog" not in at.session_state


def test_유동성을_모르는_섹터도_확정_모달이_열린다(ledger, monkeypatch):
    """🔴 창이 덜 찬 섹터의 `liquidity_ok` 는 NA 다 — `bool(pd.NA)` 가 모달을 죽였다(리뷰)."""
    import pandas as pd

    from dashboard import data as _data

    real = _data.load_scores

    def with_na():
        frame, source = real()
        frame = frame.copy()
        latest = frame["bas_dd"] == frame["bas_dd"].max()
        frame["liquidity_ok"] = frame["liquidity_ok"].astype("boolean")
        frame.loc[latest, "liquidity_ok"] = pd.NA
        return frame, source

    monkeypatch.setattr(_data, "load_scores", with_na)
    at = _as_member(_ranking_page, ledger)
    at.button(key="open_confirm").click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert "confirm_reason" in _keys(at.text_area)


def test_보관된_조에서는_랭킹에서_쓰지_못한다(ledger):
    """🔒 목록에서 감춘 조에 기록이 계속 쌓이면 되짚을 수 없다(리뷰)."""
    from sector.workspace import events

    _make_team(_run(_teams_page, ledger))
    ledger.append([events.team_archived(team_id="team_a", reason="대회 조가 바뀌었다",
                                        actor="동원", at=_FUTURE.format(1))])
    at = _open_as(_ranking_page, ledger, actor="민수")
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert not {"open_confirm", "open_attach"} & set(_keys(at.button))
    assert any("보관" in w.value for w in at.warning)


def test_제어문자가_섞인_이름은_세션에_담기지_않는다(ledger):
    """🔴 담은 뒤에 원장이 거부하면 조에 들어간 채 참가 기록만 빠진다(리뷰)."""
    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("민" + chr(31) + "수").run()
    assert any("제어문자" in e.value for e in at.error)
    assert "sc_actor" not in at.session_state


_NETWORK_ROOTS = frozenset({"requests", "urllib3", "httpx", "aiohttp", "socket", "http",
                            "huggingface_hub"})


def _network_imports(path) -> list[str]:
    """파일이 import 하는 네트워크 모듈. 🔒 문자열이 아니라 **구문 트리**로 본다 —
    `from requests import get` · `from urllib import request` 도 잡는다."""
    import ast

    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        else:
            continue
        found += [n for n in names
                  if n.split(".")[0] in _NETWORK_ROOTS or n.startswith("urllib.request")]
    return found


def test_화면_계층이_링크를_부르지_않는다():
    """🔴 SSRF 표면 0 — 근거 첨부를 그리는 코드에 네트워크 import 가 없다 (`links` 머리주석)."""
    for name in ("team_actions.py", "theme.py"):
        assert not _network_imports(ROOT / "dashboard" / name), name


def test_읽는법이_예시를_지어내지_않는다():
    """🔴 예시 숫자를 만들어 두면 팀원이 그 숫자를 실제로 인용한다.

    데이터가 있으면 **실제 값**으로 네 걸음을 그리고, 없으면 예시를 **빼고**
    그 사실을 말한다 (ADR-SC-0007). 어느 쪽이든 가짜 숫자는 나오지 않는다.
    """
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "dashboard" / "pages" / "howto.py"),
                           default_timeout=180)
    at.run()
    assert not at.exception, [str(e)[:200] for e in at.exception]


def test_등수_카드와_막대가_랭킹에_있다(app):
    """#3 — 표만으로는 21행에서 1등을 눈으로 찾아야 한다."""
    heads = [h.value for h in app.subheader]
    assert "상위 3" in heads and "점수를 한눈에" in heads
    assert "sc-rank" in _markdown(app)


# ── passcode 가 원장 밖으로 나갔다 (2026-09-12 · ADR-SC-0011 ④) ──────────────
# 🔴 화면이 해시를 손에 들지 않는다. 검증은 저장소가 하고 화면은 참·거짓만 받는다.


def test_조를_만들면_해시가_원장_밖에_있다(ledger, tmp_path):
    """🔒 계약이 아니라 **디스크에 쓰인 자리**를 본다."""
    _make_team(_run(_teams_page, ledger))
    events_dir = [p.name for p in (tmp_path / "events").glob("*.json")]
    assert len(events_dir) == 1                       # team.created 한 건
    assert (tmp_path / "secrets" / "team_a.json").is_file()

    from sector.workspace import store as store_module

    written = (tmp_path / "events" / events_dir[0]).read_text(encoding="utf-8")
    assert "passcode" not in written
    # 🔒 앱이 받는 것에는 digest 가 없다
    params = ledger.passcode_params("team_a")
    assert params.endswith("$") and params.count("$") == 5
    assert store_module.secret_path_in_repo("team_a") == "secrets/team_a.json"


def test_해시가_화면에_그려지지_않는다(ledger, tmp_path):
    """🔴 원장 읽기가 공개다 — 해시가 화면까지 오면 그것이 곧 노출 경로다."""
    import json as json_module

    at = _make_team(_run(_teams_page, ledger), passcode="산-바다-강-들")
    stored = json_module.loads(
        (tmp_path / "secrets" / "team_a.json").read_text(encoding="utf-8"))["passcode_hash"]
    digest = stored.split("$")[5]

    rendered = "\n".join(
        str(element.value) for group in (at.markdown, at.caption, at.warning,
                                         at.error, at.info, at.title, at.subheader)
        for element in group)
    assert digest not in rendered and stored not in rendered


def test_참가하면_자격증명이_세션에_남는다(ledger, tmp_path):
    """🔒 원장에 쓸 때마다 이것이 필요하다 (ADR-SC-0011 ⑤).

    🔴 평문이 아니다 — 저장된 salt 로 재계산한 값이고 그 조에서만 쓸 수 있다.
    """
    import json as json_module

    _make_team(_run(_teams_page, ledger), actor="동원", passcode="산-바다-강-들")
    stored = json_module.loads(
        (tmp_path / "secrets" / "team_a.json").read_text(encoding="utf-8"))["passcode_hash"]

    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("민수").run()
    at.text_input(key="join_passcode").input("산-바다-강-들")
    at.button(key="FormSubmitter:join-참가").click().run()

    assert at.session_state["sc_credential"] == stored
    assert "산-바다-강-들" != at.session_state["sc_credential"]


def test_나가면_자격증명도_버린다(ledger):
    """🔒 조만 지우고 남겨 두면 다음 조에 옛 자격증명을 들고 들어간다."""
    at = _make_team(_run(_teams_page, ledger), actor="동원")
    assert at.session_state["sc_credential"]
    at.button(key="leave").click().run()
    assert "sc_credential" not in at.session_state
    assert "sc_team_id" not in at.session_state


def test_옛_형식_원장이면_참가할_수_없다고_말한다(ledger, tmp_path, monkeypatch):
    """🔴 HF 원장에 옛 형식 조가 남아 있다(2026-09-12 실측 · `test_team` 1건).

    화면이 죽지도 않고 조용히 넘기지도 않는다 — **왜 참가할 수 없는지** 말한다.
    """
    from sector.workspace import events

    legacy = events.make_event(
        "team.created", team_id="team_old", actor="테스트1",
        at="2026-09-12T06:55:31+00:00", payload={"name": "테스트조"}).to_json()
    legacy["payload"]["passcode_hash"] = "scrypt$16384$8$1$c2FsdHNhbHQ$aGFzaGhhc2g"
    legacy["event_id"] = events._make(                     # noqa: SLF001 — 옛 원장 재현
        legacy["kind"], team_id=legacy["team_id"], actor=legacy["actor"],
        at=legacy["at"], payload=legacy["payload"]).event_id
    (tmp_path / "events").mkdir(parents=True, exist_ok=True)
    import json as json_module
    (tmp_path / "events" / f"{legacy['event_id']}.json").write_text(
        json_module.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    at = _run(_teams_page, ledger)
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert any("옛 형식" in str(m.value) for m in at.markdown), \
        [str(m.value)[:80] for m in at.markdown]

    # 🔒 그 조에는 실제로 참가할 수 없다 — 해시가 원장 밖에 없으므로
    at.text_input(key="identity_name").input("민수").run()
    at.text_input(key="join_passcode").input("무엇이든-넣어도")
    at.button(key="FormSubmitter:join-참가").click().run()
    assert any("passcode" in e.value for e in at.error)
    assert "sc_team_id" not in at.session_state


# ═══ M9 — 가중치 · 필터 (2026-09-17) ════════════════════════════════════════

def test_비율이_같으면_프리셋으로_돌아온다():
    """🔴 `Σw·z/Σw` 는 가중치 스칼라배에 불변이다 — 70/60/40/30 은 균형과 **점수가 같다.**

    숫자가 한 칸도 다르지 않은데 모드만 커스텀으로 남으면 근거·확정 칸이 이유 없이
    닫힌다. 그건 화면의 거짓말이다.
    """
    assert weights.Weighting.of({"M": 70, "F": 60, "B": 40, "V": 30}).name == "balanced"
    assert weights.Weighting.of({"M": 7, "F": 6, "B": 4, "V": 3}).name == "balanced"
    assert weights.Weighting.of(dict(PRESETS["contrarian"])).name == "contrarian"


def test_이름_없는_비율은_커스텀이다():
    custom = weights.Weighting.of({"M": 10, "F": 10, "B": 10, "V": 70})
    assert custom.name is None and not custom.is_preset
    with pytest.raises(weights.WeightError):
        custom.column("score")      # 🔒 커스텀에 저장된 열이 있는 척하지 않는다


def test_쓸_수_없는_가중치를_거절한다():
    """🔴 전부 0 이면 모든 섹터 점수가 `None` 이 되어 표가 통째로 빈다."""
    with pytest.raises(weights.WeightError, match="전부 0"):
        weights.Weighting.of({"M": 0, "F": 0, "B": 0, "V": 0})
    with pytest.raises(weights.WeightError):
        weights.Weighting.of({"M": -1, "F": 30, "B": 20, "V": 15})
    with pytest.raises(weights.WeightError):
        weights.Weighting.of({"M": weights.MAX_WEIGHT + 1, "F": 30, "B": 20, "V": 15})
    with pytest.raises(weights.WeightError):
        weights.Weighting.of({"M": 35, "F": 30, "B": 20})            # 축이 모자라다
    with pytest.raises(weights.WeightError, match="정수"):
        # 🔒 `bool` 은 `int` 의 하위형이다 — `True` 가 1 로 통과하면 안 된다
        weights.Weighting.of({"M": True, "F": 30, "B": 20, "V": 15})


def test_프리셋은_저장된_열을_그대로_읽는다():
    """🔴 재계산하지 않는다. HF 에 옛 파생본이 있는 동안 화면 위(표)와 아래(근거)가
    다른 숫자를 말하게 되기 때문이다 (`view.scored` 머리주석)."""
    data = frame()
    out = view.scored(data, BALANCED)
    assert list(out[view.SCORE_COLUMN]) == list(data["score_balanced_bp"])
    assert list(out[view.RANK_COLUMN]) == list(data["rank_balanced"])


def test_프리셋은_저장_열이_z_와_어긋나도_저장_열을_읽는다():
    """🔴 ADR-SC-0014 ③ 의 계약이 실제로 걸리는 **유일한** 자리다.

    게시본이 z 로 재현되는 동안에는 "저장 열을 읽는다" 와 "다시 잰다" 가 같은 답을 내서
    무엇을 하는지 구별되지 않는다. 구별되는 것은 **재게시 전 구간** — HF 에 옛 파생본이
    있어 저장 열이 z 와 어긋나는 때다. 그때 화면이 다시 재면 위쪽 표와 아래쪽 근거·
    에이전트(저장 열을 읽는다)가 **다른 숫자를 말한다.**

    🔒 그래서 여기서는 일부러 어긋난 프레임을 만든다.
    """
    data = frame()
    stale = data["score_balanced_bp"] + 7           # 옛 파생본을 흉내낸다
    data["score_balanced_bp"] = stale
    out = view.scored(data, BALANCED)
    assert list(out[view.SCORE_COLUMN]) == list(stale), "프리셋이 저장 열을 안 읽고 다시 쟀다"
    # 🔒 커스텀은 반대다 — 저장 열이 없으므로 **반드시** 다시 잰다
    only_m = weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0})
    assert list(view.scored(data, only_m)[view.SCORE_COLUMN]) == list(data["m_z_bp"])


def test_scored_는_저장된_열을_덮지_않는다():
    """🔒 덮으면 에이전트 guard 의 "`view` 를 거치지 않고 원천에서 다시 얻는다"
    (ADR-SC-0013 ④-1)가 그 순간 거짓이 된다."""
    data = frame()
    out = view.scored(data, weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0}))
    for column in ("score_balanced_bp", "rank_balanced", "score_momentum_bp"):
        assert list(out[column]) == list(data[column]), column


def test_커스텀은_배치가_쓰는_함수로_다시_잰다():
    """🔒 화면이 점수를 따로 구현하지 않는다 (`AGENTS.md` 6장)."""
    from sector.scoring import weighted_score_bp

    custom = weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0})
    out = view.scored(frame(), custom)
    for row in out.itertuples(index=False):
        again = weighted_score_bp({"M": row.m_z_bp, "F": row.f_z_bp,
                                   "B": row.b_z_bp, "V": row.v_z_bp}, custom.weights)
        assert row.score_bp == again
    # 날마다 1..N 으로 이어진다
    for _, group in out.groupby("bas_dd"):
        assert sorted(group[view.RANK_COLUMN]) == list(range(1, len(group) + 1))


def test_색인이_중복돼도_행마다_제_점수가_들어간다():
    """🔴 색인 **라벨**로 맞추면 중복 색인에서 한 값이 여러 행으로 퍼진다.

    2026-09-17 구현 점검에서 실제로 잡혔다 — 커스텀 경로가 모든 행에 같은 점수를
    **조용히** 넣었다. 그래서 `scored()` 는 라벨이 아니라 **자리**로 넣는다.
    🔒 프리셋 경로도 같은 규율이다(`.array`) — 한쪽만 고치면 다음 사람이 헷갈린다.
    """
    plain = frame()
    duplicated = frame()
    duplicated.index = [0] * len(duplicated)
    for weighting in (BALANCED, weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0})):
        expected = list(view.scored(plain, weighting)[view.SCORE_COLUMN])
        assert list(view.scored(duplicated, weighting)[view.SCORE_COLUMN]) == expected
    # 🔒 프리셋은 저장 열 그대로다 — 자리로 넣어도 값이 안 밀린다
    assert list(view.scored(duplicated, BALANCED)[view.SCORE_COLUMN]) == \
           list(plain["score_balanced_bp"])


def test_scored_는_Int64_를_지킨다():
    """🔒 `None` 이 섞인 정수 열을 pandas 가 `float64` 로 올리면 규약이 금지한 float 가
    화면 계층에 들어온다 (V26 · `AGENTS.md` 4장)."""
    data = frame()
    data.loc[data["sector_id"] == "sec_0", "m_z_bp"] = pd.NA
    only_m = weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0})
    out = view.scored(data, only_m)
    assert str(out[view.SCORE_COLUMN].dtype) == "Int64"
    assert str(out[view.RANK_COLUMN].dtype) == "Int64"
    # M 축이 없으면 살아 있는 축의 가중치가 전부 0 이라 점수가 **없다** — 0 이 아니다
    missing = out[out["sector_id"] == "sec_0"]
    assert missing[view.SCORE_COLUMN].isna().all()
    assert missing[view.RANK_COLUMN].isna().all()


def test_하루에_같은_섹터가_둘이면_던진다():
    """🔒 조용히 한 줄을 잃지 않는다 — 순위가 말없이 비뚤어진다."""
    data = pd.concat([frame(), frame().tail(1)], ignore_index=True)
    with pytest.raises(view.ViewError, match="두 번"):
        view.scored(data, weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0}))


def _liquidity_frame() -> pd.DataFrame:
    """유동성이 참·거짓·판정불가로 갈리는 최신일 프레임."""
    data = frame(n_sectors=3)
    last = data["bas_dd"] == DAYS[-1]
    data["liquidity_ok"] = data["liquidity_ok"].astype("object")
    data.loc[last & (data["sector_id"] == "sec_1"), "liquidity_ok"] = False
    data.loc[last & (data["sector_id"] == "sec_2"), "liquidity_ok"] = None
    data.loc[last & (data["sector_id"] == "sec_2"), "etf_n"] = 1
    return data


def test_유동성_판정불가는_숨기지_않는다():
    """🔴 "아직 모른다"(창이 안 참)와 "미달이다"는 다른 말이다 (ADR-SC-0007).

    한 조건에 묶으면 신규 상장 ETF 가 이유 없이 화면에서 사라진다.
    """
    data = _liquidity_frame()
    assert view.visible_ids(data, hide_illiquid=True) == frozenset({"sec_0", "sec_2"})


def test_ETF_1종_숨기기는_유동성과_별개다():
    data = _liquidity_frame()
    assert view.visible_ids(data, hide_single_etf=True) == frozenset({"sec_0", "sec_1"})
    assert view.visible_ids(data, hide_illiquid=True,
                            hide_single_etf=True) == frozenset({"sec_0"})


def test_GICS_필터는_고른_대분류만_남긴다():
    data = frame()
    data.loc[data["sector_id"] == "sec_0", "gics"] = "Materials"
    assert view.visible_ids(data, gics=frozenset({"Materials"})) == frozenset({"sec_0"})
    # 🔒 빈 선택은 "전체" 다 — 0개를 남기지 않는다
    assert len(view.visible_ids(data, gics=frozenset())) == 3


def test_GICS_선택지는_프레임에_있는_것만_준다():
    """🔒 없는 대분류를 목록에 두면 고르는 순간 표가 비고, 사용자는 이유를 모른다."""
    data = frame()
    data.loc[data["sector_id"] == "sec_0", "gics"] = "Materials"
    assert view.gics_options(data, KOREAN) == [("Industrials", "산업재"),
                                               ("Materials", "Materials")]


def test_필터를_걸어도_등수와_축순위가_전체_기준이다():
    """🔴 이것이 필터의 핵심 계약이다. 고른 범위에서 다시 매기면 "F 축 1위" 가
    "고른 셋 중 1위" 가 되고, 화면은 그 차이를 말하지 않는다."""
    data = view.scored(frame(), BALANCED)
    full = view.podium(data, weighting=BALANCED, top=3)
    only_last = view.podium(data, weighting=BALANCED, top=3, only=frozenset({"sec_0"}))
    assert [e["sector_id"] for e in full] == ["sec_2", "sec_1", "sec_0"]
    assert len(only_last) == 1 and only_last[0]["sector_id"] == "sec_0"
    # 걸러도 등수·축 순위가 그대로다 — 1위가 되지 않는다
    tail = next(e for e in full if e["sector_id"] == "sec_0")
    assert only_last[0]["rank"] == tail["rank"] == 3
    assert only_last[0]["score_bp"] == tail["score_bp"]


def test_막대도_걸러도_순위_순서를_지킨다():
    data = view.scored(frame(), BALANCED)
    bars = view.score_bars(data, names=KOREAN, only=frozenset({"sec_0", "sec_2"}))
    assert len(bars) == 2
    assert list(bars.index) == [KOREAN.sector_label("sec_2"), KOREAN.sector_label("sec_0")]


# ── 성능 — 팀 7명이 매일 쓴다 (이슈 #1 · #2) ────────────────────────────────

def test_커스텀_재계산이_행_수에_비례해_pandas_를_타지_않는다():
    """🔴 `itertuples` 를 날짜별 그룹마다 부르면 pandas 가 **그룹마다 열 수만큼** `_ixs` 를
    탄다 — 285그룹 × 26열 = 7,411번, 361ms. 슬라이더는 한 칸 움직일 때마다 rerun 이라
    그대로 체감 지연이 된다 (이슈 #1).

    🔒 **시간을 재지 않는다** — 느린 CI 에서 흔들린다. 대신 **행 단위 접근 횟수**를 센다.
    🔴 소스에서 `itertuples` 글자를 찾는 검사로는 모자랐다(적대적 리뷰) — 헬퍼로 빼거나
       `.iloc` 행 루프로 바꾸거나 `apply(axis=1)` 로 쓰면 **초록불인 채로** 무장해제된다.
       그래서 행 수만 다른 두 프레임에서 **호출 수가 늘지 않는 것**을 본다.
    """
    import pandas as pd

    counted = {"n": 0}
    real = pd.DataFrame._ixs

    def watched(self, i, axis=0):
        counted["n"] += 1
        return real(self, i, axis=axis)

    only_m = weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0})
    taken = []
    for n_sectors in (3, 12):
        data = frame(n_sectors)
        counted["n"] = 0
        pd.DataFrame._ixs = watched
        try:
            view.scored(data, only_m)
        finally:
            pd.DataFrame._ixs = real
        taken.append((len(data), counted["n"]))

    (small_rows, small_hits), (big_rows, big_hits) = taken
    assert big_rows > small_rows * 3, taken          # 픽스처가 실제로 커졌는가
    # 🔒 행이 4배로 늘어도 행 단위 접근은 **거의 그대로**여야 한다
    assert big_hits <= small_hits + 2, (
        f"행 {small_rows}→{big_rows} 인데 행 단위 접근이 {small_hits}→{big_hits} 로 늘었다 — "
        f"열을 통째로 꺼내 쓰지 않는다")


def _with_gaps() -> "pd.DataFrame":
    """z 가 군데군데 빈 판. 🔴 **`_int_list` 의 존재 이유가 결측이다** — 결측이 없는
    픽스처로는 `pd.NA`→`None` 변환이 한 번도 검증되지 않는다(적대적 리뷰)."""
    data = frame(n_sectors=4)
    last = data["bas_dd"] == DAYS[-1]
    for column in ("m_z_bp", "f_z_bp", "b_z_bp", "v_z_bp"):
        data[column] = data[column].astype("Int64")
    data.loc[data["sector_id"] == "sec_0", "m_z_bp"] = pd.NA
    data.loc[last & (data["sector_id"] == "sec_1"), ["f_z_bp", "b_z_bp"]] = pd.NA
    # 🔒 축이 하나도 없는 행 — 실데이터에 399행 있다
    data.loc[last & (data["sector_id"] == "sec_3"),
             ["m_z_bp", "f_z_bp", "b_z_bp", "v_z_bp"]] = pd.NA
    return data


def test_커스텀_재계산이_배치_값과_한_칸도_다르지_않다():
    """🔒 빠르게 만들면서 답이 바뀌면 아무 소용이 없다. 두 경로를 **전 행** 대조한다.

    🔒 **점수와 순위를 둘 다** 본다 — 점수만 보면 `rank` 를 전부 1 로 바꿔도 통과한다.
    🔒 결측이 섞인 판으로도 돌린다 (`_with_gaps`).
    """
    from sector.scoring import rank_scores, weighted_score_bp

    for data in (frame(n_sectors=3), _with_gaps()):
        for raw in ({"M": 100, "F": 0, "B": 0, "V": 0}, {"M": 10, "F": 10, "B": 10, "V": 70},
                    {"M": 1, "F": 1, "B": 1, "V": 1}):
            weighting = weights.Weighting.of(raw)
            out = view.scored(data, weighting)
            for day, group in out.groupby("bas_dd"):
                again = {
                    str(row.sector_id): weighted_score_bp(
                        {a: view.int_or_none(getattr(row, f"{view._AXIS_PREFIX[a]}_z_bp"))
                         for a in AXES}, weighting.weights)
                    for row in group.itertuples(index=False)}
                ranked = rank_scores(again)
                for row in group.itertuples(index=False):
                    sid = str(row.sector_id)
                    assert view.int_or_none(row.score_bp) == again[sid], (raw, day, sid)
                    assert view.int_or_none(row.rank) == ranked[sid], (raw, day, sid)
            assert str(out[view.SCORE_COLUMN].dtype) == "Int64"
            assert str(out[view.RANK_COLUMN].dtype) == "Int64"


def test_살아있는_축이_하나도_없으면_점수가_없다():
    """🔴 0 이 아니라 **없음**이다 (ADR-SC-0007). 결측을 0 으로 채우면 그 섹터가
    가운데로 올라와 순위가 통째로 거짓이 된다."""
    out = view.scored(_with_gaps(), weights.Weighting.of({"M": 1, "F": 1, "B": 1, "V": 1}))
    blank = out[(out["bas_dd"] == DAYS[-1]) & (out["sector_id"] == "sec_3")]
    assert blank[view.SCORE_COLUMN].isna().all()
    assert blank[view.RANK_COLUMN].isna().all()


def test_점수_캐시의_수명이_고정돼_있다():
    """🔴 TTL 은 이 변경의 **유일한 운용 파라미터**다 — 너무 길면 배치가 게시해도 팀이
    못 본다(이슈 #2). `ttl=None` 으로 바꿔도 다른 테스트는 전부 통과한다."""
    from dashboard import data as _data

    assert 0 < _data.SCORES_TTL_SECONDS <= 600, "게시 주기(하루 1회)에 비해 너무 길다"
    info = getattr(_data.load_scores, "_info", None)
    assert info is not None, "`load_scores` 가 캐시되지 않았다"
    assert info.ttl == _data.SCORES_TTL_SECONDS


def test_점수를_읽는_동안_화면이_말을_한다():
    """🔴 첫 로드는 실측 **6,367ms** 다(HF `latest/` · 5,985행). 그동안 안내가 없으면
    팀원 7명은 "멈췄다" 고 읽는다 — 그들은 개발자가 아니다 (이슈 #8).

    🔒 **렌더된 스피너로는 검사할 수 없다.** Streamlit 의 스피너는 transient 라
       `AppTest` 가 `new_transient` 델타를 통째로 건너뛴다
       (`streamlit/testing/v1/element_tree.py`). 그래서 계약을 **캐시 설정**에서
       고정한다 — 바로 위 TTL 을 지키는 것과 같은 방식이다. 못 보는 것을 본 척하는
       테스트보다, 볼 수 있는 곳에서 정확히 거는 편이 낫다.

    🔒 `True` 를 거른다. `show_spinner=True` 도 "말을 하긴" 하지만 Streamlit 이
       ``Running `load_scores()`.`` 라는 영어 함수 이름을 그린다.
    """
    from dashboard import data as _data

    info = getattr(_data.load_scores, "_info", None)
    assert info is not None, "`load_scores` 가 캐시되지 않았다"
    assert isinstance(info.show_spinner, str), (
        "읽는 동안 화면이 아무 말도 하지 않는다 — `show_spinner` 에 **문구**를 준다")
    assert info.show_spinner.strip(), "문구가 비어 있다"
    assert info.show_spinner == _data.SCORES_SPINNER


def test_캐시가_빌_때만_스피너가_돈다(monkeypatch):
    """🔴 **설정만 보면 중첩 함정을 못 잡는다.** Streamlit 은 캐시 함수가 다른 캐시 함수
    **안에서** 불리면 스피너를 조용히 끈다(`is_nested_cache_function` · `cache_utils`).
    그때도 `_info.show_spinner` 는 그대로라 바로 위 테스트는 통과하고, 화면만 다시
    말을 잃는다. 그래서 미스 경로에서 **우리 문구로 실제 열리는지**까지 본다.

    🔒 히트 경로에서는 열리지 않아야 한다 — 평소 rerun 에 비용이 붙으면 이슈 #2 가
       고친 지점으로 되돌아간다.
    """
    import contextlib

    from streamlit.elements.spinner import SpinnerMixin

    from dashboard import data as _data

    opened: list[str] = []

    @contextlib.contextmanager
    def recording(self, text: str = "In progress...", **kwargs):
        # 🔒 `cache_utils` 는 문구를 **위치 인자**로 준다 — `_cache`·`show_time` 만 kwargs 다
        opened.append(text)
        yield

    monkeypatch.setattr(SpinnerMixin, "spinner", recording)
    monkeypatch.setattr(_data, "_from_hf", lambda: None)
    monkeypatch.setattr(_data, "_from_local",
                        lambda: (frame(), _data.Source(kind="local", label="테스트용")))

    clear = getattr(_data.load_scores, "clear", lambda: None)
    clear()
    try:
        _data.load_scores()
        assert opened == [_data.SCORES_SPINNER], f"미스인데 스피너가 이렇게 돌았다: {opened}"
        _data.load_scores()
        assert opened == [_data.SCORES_SPINNER], "캐시 히트인데도 스피너가 돌았다"
    finally:
        clear()


def test_점수를_매_rerun_마다_다시_읽지_않는다(monkeypatch):
    """🔴 `load_scores` 는 HF ETag 왕복 + 745KB + `read_parquet` 이라 220ms 다.
    위젯을 하나 만질 때마다 그것이 돌면 슬라이더 한 칸에 네트워크 왕복이 붙는다.

    🔒 **뒤끝을 남기지 않는다** — 가짜를 캐시에 남기면 뒤따르는 AppTest 가 그것을 읽는다.
       그래서 앞뒤로 비운다(`pytest-randomly` 로 순서가 섞여도 안전하게).
    """
    from dashboard import data as _data

    # 🔒 캐시가 **없을 때도** 의도한 단언이 울려야 한다. `clear()` 를 그냥 부르면
    #    데코레이터가 사라진 순간 `AttributeError` 로 먼저 죽어, 실패 메시지가
    #    "캐시가 없다" 가 아니라 엉뚱한 것이 된다
    clear = getattr(_data.load_scores, "clear", lambda: None)
    reads: list[int] = []

    def fake_local():
        reads.append(1)
        return frame(), _data.Source(kind="local", label="테스트용")

    monkeypatch.setattr(_data, "_from_hf", lambda: None)
    monkeypatch.setattr(_data, "_from_local", fake_local)
    clear()
    try:
        first, _ = _data.load_scores()
        second, _ = _data.load_scores()
        assert reads == [1], f"원천을 {len(reads)}번 읽었다 — 캐시가 없다"
        assert first.equals(second)
        # 🔒 사본을 준다 — 화면이 고쳐도 캐시가 더러워지지 않는다
        first.loc[0, "score_balanced_bp"] = 999_999
        third, _ = _data.load_scores()
        assert int(third.loc[0, "score_balanced_bp"]) != 999_999
    finally:
        clear()


def test_읽지_못한_것은_캐시하지_않는다(monkeypatch):
    """🔒 토큰이 없어 실패하는 동안 시크릿을 채우면 **다음 rerun 에 바로** 읽혀야 한다.
    예외가 캐시되면 TTL 이 끝날 때까지 빈 화면이 남는다."""
    from dashboard import data as _data

    monkeypatch.setattr(_data, "_from_hf", lambda: None)
    monkeypatch.setattr(_data, "_from_local", lambda: None)
    clear = getattr(_data.load_scores, "clear", lambda: None)
    clear()
    try:
        with pytest.raises(_data.DataUnavailable):
            _data.load_scores()
        monkeypatch.setattr(_data, "_from_local",
                            lambda: (frame(), _data.Source(kind="local", label="테스트용")))
        recovered, _ = _data.load_scores()
        assert len(recovered) > 0, "예외가 캐시돼 복구되지 않았다"
    finally:
        clear()


# ── 랭킹 화면 — 커스텀 가중치의 경계 (M9) ───────────────────────────────────

def _app_with(**state):
    """세션 상태를 미리 심은 AppTest. 🔒 위젯이 그려지기 **전**에 심는다."""
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=120)
    for key, value in state.items():
        at.session_state[key] = value
    at.run()
    return at


def _app_with_frame(monkeypatch, data_frame, **state):
    """합성 프레임을 **원천으로 꽂고** 앱을 띄운다 — 실제 파생본에 없는 모양을 보려고.

    🔒 캐시는 `conftest` 의 autouse 가 앞뒤로 비운다. 여기서 또 비우지 않는다 —
       비우는 규율이 두 곳에 있으면 한 곳이 조용히 썩는다.
    """
    from dashboard import data as _data

    monkeypatch.setattr(_data, "_from_hf", lambda: None)
    monkeypatch.setattr(
        _data, "_from_local",
        lambda: (data_frame.copy(), _data.Source(kind="local", label="테스트용")))
    return _app_with(**state)


CUSTOM_STATE = {"rank_custom": True, "rank_w_M": 10, "rank_w_F": 10,
                "rank_w_B": 10, "rank_w_V": 70}


def _watch_sector_blocks(monkeypatch) -> list[str]:
    """근거·확정 칸이 그려졌는지 **호출로** 센다.

    🔒 문구를 보지 않는다 — 문구만 보면 나중에 블록이 되살아나도 통과한다.
    """
    from dashboard import evidence as evidence_module
    from dashboard import team_actions as actions_module

    calls: list[str] = []
    monkeypatch.setattr(evidence_module, "render_evidence",
                        lambda *a, **k: calls.append("evidence"))
    monkeypatch.setattr(actions_module, "render_sector_actions",
                        lambda *a, **k: calls.append("actions"))
    return calls


def test_커스텀_가중치에서는_근거를_열지_않는다(monkeypatch):
    """🔴 에이전트는 근거의 출처를 `rank_{profile}` 이라는 **저장 열 이름**으로 적고
    guard 가 그 열에서 값을 다시 얻어 대조한다(ADR-SC-0013 ④-1). 이름 없는 비율에는
    그 열이 없다.

    🔒 문구가 아니라 **호출**을 본다 — 문구만 보면 나중에 블록이 되살아나도 통과한다.
    """
    calls = _watch_sector_blocks(monkeypatch)

    custom = _app_with(**CUSTOM_STATE)
    assert not custom.exception, [str(e)[:200] for e in custom.exception]
    # 🔒 ADR-SC-0014 ④ 가 닫는 것은 **근거와 확정 둘 다**다. 하나만 보면 반쪽이다
    assert calls == [], f"커스텀 가중치에서 {calls} 를 그렸다"
    from dashboard.pages.ranking import CUSTOM_NOTICE
    assert any(CUSTOM_NOTICE == i.value for i in custom.info), [i.value for i in custom.info]

    calls.clear()
    preset = _app_with(rank_custom=False)
    assert not preset.exception, [str(e)[:200] for e in preset.exception]
    assert set(calls) == {"evidence", "actions"}, f"프리셋에서 {calls} 만 그렸다"


def test_프리셋과_같은_비율이면_근거가_다시_열린다(monkeypatch):
    """🔒 슬라이더를 균형 값으로 맞추면 커스텀 모드에 남지 않는다 — 점수가 같기 때문이다."""
    calls = _watch_sector_blocks(monkeypatch)
    at = _app_with(rank_custom=True, rank_w_M=70, rank_w_F=60, rank_w_B=40, rank_w_V=30)
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert calls, "균형과 같은 비율인데 근거가 닫혔다"


def test_돌아가기_버튼이_고른_프리셋으로_간다():
    """🔴 화면이 지금 쓰는 가중치와 **다른 가중치를 적으면 안 된다** (ADR-SC-0014 ④).

    예전에는 콜백이 `"balanced"` 를 박아 두고 라디오를 안 건드려, 모멘텀을 고른 채
    커스텀에서 이 버튼을 누르면 — 버튼은 "균형", 캡션은 **모멘텀 55/25/15/5**,
    슬라이더는 **균형 35/30/20/15** 를 보여 줬다. 셋이 서로 다른 말을 했다.
    """
    at = _app_with(rank_profile="momentum", **CUSTOM_STATE)
    assert not at.exception, [str(e)[:200] for e in at.exception]
    back = [b for b in at.button if "돌아가기" in b.label]
    assert len(back) == 1, [b.label for b in at.button]
    label = explain.preset_label("momentum")
    assert label in back[0].label, back[0].label      # 🔒 라벨이 돌아갈 곳을 말한다

    at = back[0].click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert any(label in c.value for c in at.caption), [c.value for c in at.caption]
    assert {s.label: s.value for s in at.slider} == {
        f"{AXIS_NAMES[a]} ({a})": PRESETS["momentum"][a] for a in AXES}
    assert at.radio[0].value == "momentum"
    assert not [b for b in at.button if "돌아가기" in b.label], "커스텀에서 안 빠져나왔다"


def test_슬라이더가_네_축_모두에_있다():
    at = _app_with()
    assert {s.label for s in at.slider} == {
        f"{AXIS_NAMES[a]} ({a})" for a in AXES}, [s.label for s in at.slider]


def test_필터가_행을_줄이되_점수는_그대로다():
    """🔒 필터는 행을 숨길 뿐이다 — 순위 번호가 이어지지 않는 것이 **정상**이다."""
    at = _app_with(rank_hide_single=True)
    assert not at.exception, [str(e)[:200] for e in at.exception]
    assert any("개 표시" in c.value for c in at.caption)


# ── 구조 — 조용히 실패할 수 있는 경로를 소스로 못박는다 ──────────────────────

def test_하단에는_원본_프레임을_넘긴다():
    """🔴 `sector_story` 의 `total` 이 "21개 중 3위" 의 21 이다. 걸러진 프레임이나
    `scored()` 를 지난 프레임이 새면 그 문장이 "5개 중 1위" 가 된다.
    """
    source = (ROOT / "dashboard" / "pages" / "ranking.py").read_text(encoding="utf-8")
    for call in ("_render_breakdown(", "team_actions.render_sector_actions("):
        line = next(l for l in source.splitlines() if call in l and "def " not in l)
        first = line.split(call, 1)[1].split(",")[0].strip()
        assert first == "frame", f"{call} 의 첫 인자가 {first!r} 다 — 원본이어야 한다"


def test_위젯을_그린_뒤에_session_state_를_쓰지_않는다():
    """🔴 Streamlit 은 **위젯 키**에 대한 대입이 그 위젯 생성 뒤에 오면
    `StreamlitWidgetAlreadyInstantiatedError` 를 던진다. `evidence._reset_on_new_sector`
    가 주석으로만 지키던 규율(*"위젯을 그리기 전에 부른다"*)을 테스트로 내린다.

    🔒 "위젯 키인가" 는 정적으로 못 가른다 — 그래서 **쓰는 자리를 통째로 적어 둔다.**
       새 자리가 생기면 여기가 깨지고, 왜 안전한지를 적어야 통과한다. 그게 요점이다.
    """
    import ast

    # (파일, 함수) → 왜 안전한가
    allowed = {
        ("ranking.py", "_sync_sliders"): "버튼 콜백 — 스크립트 본문보다 먼저 돈다",
        ("ranking.py", "_leave_custom"): "버튼 콜백",
        ("ranking.py", "_weight_controls"): "위젯을 그리기 전 `setdefault` — 아래 테스트가 순서를 본다",
        ("ranking.py", "_apply_link"): "🔒 **모든 위젯보다 앞**에서 돈다 — `keep` 을 "
                                       "`view.visible_ids`(순수 함수)로 내므로 위젯이 필요 없다",
        ("ranking.py", "_share_button"): "`_REPORT` `pop` — 위젯 키가 아니다",
        ("evidence.py", "_fill"): "버튼 콜백",
        ("evidence.py", "_reset_on_new_sector"): "위젯을 그리기 전에 부른다 (`pop` 포함)",
        ("evidence.py", "render_evidence"): "인계 기록 — 위젯 키가 아니다",
        ("session.py", "set_actor"): "위젯 키가 아니다",
        ("session.py", "set_team"): "위젯 키가 아니다",
        ("session.py", "set_credential"): "위젯 키가 아니다",
        ("team_actions.py", "_open"): "모달 플래그 — 위젯 키가 아니다",
        ("team_actions.py", "_succeed"): "플래시 — 위젯 키가 아니다",
        ("team_actions.py", "_close"): "모달 플래그 `pop` — 위젯 키가 아니다",
        ("team_actions.py", "show_flash"): "플래시 `pop` — 위젯 키가 아니다",
        ("session.py", "leave"): "조·자격증명 `pop` — 위젯 키가 아니다",
    }
    def _is_state(node) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == "session_state"

    #: 🔒 대입만 보지 않는다 — `setdefault`·`update` 도 위젯 뒤에 오면 똑같이 던진다
    writing_methods = {"setdefault", "update", "pop", "clear"}
    found = set()
    for path in (ROOT / "dashboard").rglob("*.py"):
        if path.name.endswith("_test.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in [n for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for node in ast.walk(function):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target] if isinstance(node, ast.AugAssign) else [])
                for target in targets:
                    if isinstance(target, ast.Subscript) and _is_state(target.value):
                        found.add((path.name, function.name))
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in writing_methods and _is_state(node.func.value)):
                    found.add((path.name, function.name))
    assert found == set(allowed), (
        f"session_state 에 쓰는 자리가 바뀌었다 — 새로 생긴 것 {found - set(allowed)} · "
        f"사라진 것 {set(allowed) - found}. 콜백이거나 위젯 키가 아님을 확인하고 적는다")


# ── 랭킹 화면 — 점수 없는 섹터 (이슈 #3) ────────────────────────────────────
# 🔴 **한 섹터의 빈칸이 나머지 20개까지 가리면 안 된다.** 세 자리가 그랬고 세 자리 다
#    `TypeError` 로 페이지를 통째로 죽였다 — 셀렉트박스 `format_func` · 막대 눈금
#    `_floor`/`_ceil` · '셋 다 상위' 의 `nsmallest`. 앞의 둘은 이슈가 짚었고
#    **세 번째는 이 AppTest 가 찾았다** — 순수 함수 테스트로는 안 보이는 자리다.

def test_최신일_일부가_결측이어도_랭킹이_그려진다(monkeypatch):
    """🔴 `sectors.yaml` 에 섹터를 하나만 추가해도 그날 이 모양이 된다 (`blank_latest`)."""
    at = _app_with_frame(monkeypatch, blank_latest(3, blanks=1))
    assert not at.exception, [str(e)[:300] for e in at.exception]

    # 🔒 **숨기지 않는다.** 점수가 없다는 것도 사실이고, 화면은 `—` 로 말한다
    options = at.selectbox[0].options
    assert "— · sec_0" in options, options
    assert [o for o in options if o.startswith("1위 · ")], options

    # 🔒 '셋 다 상위' 가 살아 있다 — 순위가 있는 섹터가 5개보다 적은 자리다
    consensus = [m.value for m in at.markdown if m.value.startswith("- **sec_")]
    assert consensus, [m.value[:60] for m in at.markdown]
    # 🔴 **예외 0 만 보면 안 된다.** `nsmallest` 의 `notna()` 를 지워도 페이지는 죽지 않고
    #    순위 없는 섹터가 "셋 다 상위" 에 **섞여 들어온다** — 등수 칸이 `—` 인 채로.
    #    돌연변이 검사에서 실제로 그랬다(2026-09-18). 그리는 내용을 본다
    assert not any("sec_0" in line for line in consensus), consensus
    # 🔒 줄마다 **프리셋 수만큼 등수**가 있다. `rank_badge` 가 `—` 를 그렸다면 0 개다
    #    (구분자도 `—` 라 글자로는 못 가른다 — 세는 것은 `위` 다)
    assert all(line.count("위") == len(view.PROFILES) for line in consensus), consensus


def test_결측_섹터를_골라도_근거_칸이_죽지_않는다(monkeypatch):
    """🔒 기본 선택은 1위라 결측 경로를 안 밟는다 — **골라서** 밟는다.

    🔴 그리고 값을 지어내지 않는다 (ADR-SC-0007) — "점수를 낼 수 없다" 고 말한다.
    """
    at = _app_with_frame(monkeypatch, blank_latest(3, blanks=1), rank_detail="sec_0")
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert at.selectbox[0].value == "sec_0"
    drawn = " ".join(m.value for m in at.markdown)
    assert "점수를 낼 수 없" in drawn


def test_최신일이_전부_결측이어도_랭킹이_그려진다(monkeypatch):
    """🔴 이때 `_floor` 가 `min(pd.NA, 0)` 을 해서 표를 못 그렸다."""
    at = _app_with_frame(monkeypatch, blank_latest(3))
    assert not at.exception, [str(e)[:300] for e in at.exception]

    options = at.selectbox[0].options
    assert all(o.startswith("— · ") for o in options), options
    # 🔒 등수를 지어내지 않는다 — 없으면 없다고 한다
    assert any("순위를 낼 수 있는 섹터가 없다" in m.value for m in at.markdown)


def test_막대_눈금이_결측만_있어도_무너지지_않는다():
    """🔴 `min(pd.NA, 0)` 은 `boolean value of NA is ambiguous` 다. 눈금 한 칸이 표를 죽였다.

    🔒 전부 결측일 때 `0~1` 로 두는 것은 **값이 아니라 눈금**이다 — 막대는 전부 비어
       그려지고, "값이 없다" 는 사실이 화면에 그대로 남는다.
    """
    from dashboard.pages import ranking as ranking_page

    blank = pd.Series(pd.array([None, None], dtype="Int64"))
    assert (ranking_page._floor(blank), ranking_page._ceil(blank)) == (0, 1)

    mixed = pd.Series(pd.array([-3000, None, 4000], dtype="Int64"))
    assert (ranking_page._floor(mixed), ranking_page._ceil(mixed)) == (-3000, 4000)


def test_결측_칸을_내리는_문이_하나다():
    """🔒 `view.int_or_none` 이 **공개**인 이유 — 화면·에이전트가 각자 캐스팅하면
       한 곳만 고쳐도 다른 곳이 같은 `TypeError` 로 죽는다 (실제로 세 자리였다)."""
    assert view.int_or_none(pd.NA) is None
    assert view.int_or_none(None) is None
    assert view.int_or_none(float("nan")) is None
    assert view.int_or_none(pd.array([7], dtype="Int64")[0]) == 7
    assert "int_or_none" in view.__all__



# ── 공유 링크 (M9c) ─────────────────────────────────────────────────────────
# 🔴 URL 은 팀원이 링크로 받는 **외부 글**이다 (ADR-SC-0012 ④ 의 목록에 더한다).
#    그래서 여기 테스트의 요점은 두 가지다 — ① 쓸 수 없는 값이 **화면을 죽이지 않는다**
#    ② 그 값의 **글자가 어디에도 새지 않는다.**

_PROFILES = list(PRESETS)
_GICS = ["Materials", "Industrials"]
_SECTORS = ["steel", "robot"]


def _parse(raw, *, as_of="20260909"):
    from dashboard import share

    return share.parse(raw, profiles=_PROFILES, gics_ids=_GICS, sector_ids=_SECTORS,
                       as_of=as_of, default_profile="balanced")


#: 🔴 키 **이름과 값 양쪽**에 넣는다. 값 쪽에만 넣으면 `ignored` 에 `f"{key}={value}"` 를
#:    담는 돌연변이가 살아남고, 이름 쪽에만 넣으면 그 키는 `unknown`(개수)으로 세어져
#:    통과한다 — 적대적 설계 리뷰가 짚은 자리다.
_PAYLOAD = "<script>alert(1)</script>"

#: 🔴 **한 종류로는 부족했다.** `<script>…` 만 넣은 테스트가 통과하는 동안
#:    `?w=M²-F30-B20-V15` 한 줄이 페이지를 죽였다 — `"²".isdigit()` 은 `True` 인데
#:    `int("²")` 는 던진다(적대적 구현 리뷰). 계열을 갈라 둔다.
_NASTY = [
    "<script>alert(1)</script>",     # 마크다운·HTML
    "M²-F30-B20-V15",                # isdigit 은 참, int 는 던진다
    "M" + "9" * 5000,                # 파이썬 3.12 의 4300자리 한도
    "٣٥",                            # 아랍 숫자 — isdecimal 도 참이다
    "２０２６０９０９",                # 전각 숫자
    "00000000",                      # 모양은 날짜, 실재하지 않는 날짜
    "",                              # 빈 값
    "-" * 200,                       # 구분자만
]


def test_링크의_글자가_Parsed_어디에도_새지_않는다():
    """🔒 이것이 `Parsed` 의 필드를 `Literal`·`int` 로 좁혀 둔 이유다."""
    parsed = _parse({
        "profile": [_PAYLOAD], "w": [_PAYLOAD], "gics": [_PAYLOAD, "Materials"],
        "illiquid": [_PAYLOAD], "single": [_PAYLOAD], "sector": [_PAYLOAD],
        "as_of": [_PAYLOAD], _PAYLOAD: [_PAYLOAD],
    })
    blob = repr(parsed)
    assert _PAYLOAD not in blob, blob
    assert "script" not in blob, blob
    # 🔒 그래도 **무엇을 못 썼는지는 말한다** — 침묵이 `bind` 를 기각한 이유다
    assert set(parsed.ignored) == {"profile", "w", "illiquid", "single", "sector",
                                   "as_of", "gics"}, parsed.ignored
    assert parsed.unknown == 1


@pytest.mark.parametrize("value", _NASTY)
@pytest.mark.parametrize("key", ["profile", "w", "gics", "illiquid", "single",
                                 "sector", "as_of"])
def test_어느_키에_무엇이_와도_파싱이_던지지_않는다(key, value):
    """🔴 `parse` 는 순수 함수라 **아무도 잡아 주지 않는다.** 그리고 적용이 모든 위젯보다
    앞이라, 여기서 예외가 나면 화면이 한 칸도 안 그려진다.

    🔒 `?w=M²-F30-B20-V15` 가 실제로 그렇게 죽였다 — `isdigit()` 이 `int()` 와 다른
       집합이기 때문이다(`share._is_number` 의 머리주석).
    """
    parsed = _parse({key: [value]})
    # 🔒 그리고 **글자가 새지 않는다**
    assert value not in repr(parsed) or value == "", repr(parsed)


def test_쓸_수_없는_값은_기본값이_되고_키_이름만_남는다():
    parsed = _parse({"profile": ["없는프리셋"], "sector": ["없는섹터"]})
    assert parsed.share.profile == "balanced"
    assert parsed.share.sector is None
    assert set(parsed.ignored) == {"profile", "sector"}


@pytest.mark.parametrize("value,expected,ignored", [
    ("1", True, False),
    ("0", False, False),
    ("", False, True),
    ("yes", False, True),
    ("true", False, True),
    ("TRUE", False, True),
    ("2", False, True),
])
def test_illiquid_는_1과_0만_받는다(value, expected, ignored):
    """🔴 `bool(value)` 로 읽으면 `?illiquid=0` 이 필터를 **켠다.**

    🔒 왕복 테스트는 이 경로를 **절대 밟지 않는다** — 꺼짐이면 키를 빼기 때문에
       `parse(encode(s))` 가 `"0"` 을 한 번도 만들지 않는다. 그래서 표로 따로 고정한다.
    """
    parsed = _parse({"illiquid": [value]})
    assert parsed.share.hide_illiquid is expected
    assert ("illiquid" in parsed.ignored) is ignored


@pytest.mark.parametrize("value", [
    "M35-F30-B20",          # 축이 셋
    "M35-F30-B20-V15-X5",   # 모르는 축
    "M35-F30-B20-M15",      # 축 중복
    "M35-F30-B20-V101",     # 상한 초과
    "M35-F30-B20-V-5",      # 음수 — `-` 가 구분자라 토큰이 깨진다
    "M35-F30-B20-Vabc",     # 숫자가 아니다
    "35-30-20-15",          # 축 글자가 없다 (손으로 쓴 링크의 순서 착오)
    "",
])
def test_가중치는_전부_또는_무효다(value):
    """🔒 일부만 받으면 나머지를 **어디선가 채워야** 하고, 그것이 곧 값을 지어내기다."""
    parsed = _parse({"w": [value]})
    assert parsed.share.weights is None
    assert parsed.share.custom is False
    assert "w" in parsed.ignored


def test_네_축이_전부_0_인_상태도_링크에_담긴다():
    """🔴 슬라이더로 **도달 가능한 상태**다. 그때 화면은 오류를 내고 프리셋으로 그린다.

    파싱이 그것을 거절하면 링크가 그 화면을 재현하지 못한다 — 그래서
    `share._weights` 는 `weights.normalize` 를 부르지 않는다. 전부-0 판정은 화면이 한다.
    """
    parsed = _parse({"w": ["M0-F0-B0-V0"]})
    assert parsed.share.custom is True
    assert parsed.share.weights == {a: 0 for a in AXES}
    assert "w" not in parsed.ignored
    # 🔒 그리고 화면 쪽 함수는 여전히 거절한다 — 두 계층의 역할이 갈려 있다
    with pytest.raises(weights.WeightError):
        weights.Weighting.of(parsed.share.weights)


def test_아는_gics_만_남고_버린_것이_있으면_말한다():
    parsed = _parse({"gics": ["Materials", "없는대분류", "Industrials"]})
    assert parsed.share.gics == frozenset({"Materials", "Industrials"})
    assert "gics" in parsed.ignored


def test_gics_가_전부_유효하면_아무_말도_안_한다():
    parsed = _parse({"gics": ["Industrials", "Materials"]})
    assert parsed.ignored == ()


def test_반복_파라미터의_마지막_값을_쓴다():
    """🔒 Streamlit 의 매핑 접근과 같은 규칙이다 — 두 규칙이 있으면 화면과 링크가 갈린다."""
    assert _parse({"profile": ["balanced", "momentum"]}).share.profile == "momentum"


def test_링크의_기준일이_다르면_말하고_같으면_잠잠하다():
    """🔒 옛 날짜를 **그리지는 않는다** — 그럴 데이터 경로가 없다. 말할 뿐이다."""
    assert _parse({"as_of": ["20260901"]}, as_of="20260909").stale_as_of == "20260901"
    assert _parse({"as_of": ["20260909"]}, as_of="20260909").stale_as_of is None
    # 🔒 날짜 모양이 아니면 그것도 무시하고 키 이름만 말한다
    bad = _parse({"as_of": ["2026-09-01"]})
    assert bad.stale_as_of is None and "as_of" in bad.ignored


@pytest.mark.parametrize("value", ["00000000", "20261345", "٣٥٦٧٨٩٠１",
                                   "２０２６０９０９", "2026-09-01", "2026090"])
def test_기준일은_실재하는_날짜만_받는다(value):
    """🔴 `len==8 and isdigit()` 로는 부족했다 — 위 값들이 전부 통과해 화면의 경고
    문장에 **그대로 그려졌다**(적대적 구현 리뷰). 모양이 아니라 날짜를 본다.
    """
    parsed = _parse({"as_of": [value]})
    assert parsed.stale_as_of is None
    assert "as_of" in parsed.ignored


def test_링크가_지금_표보다_앞서면_방향을_바꿔_말한다():
    """🔴 방향을 안 재면 거짓이 된다 — `load_scores` 가 HF→로컬로 폴백하면
    **내 표가 링크보다 옛날**일 수 있다.
    """
    behind = _parse({"as_of": ["20260901"]}, as_of="20260909")
    assert behind.stale_as_of == "20260901" and behind.stale_is_ahead is False

    ahead = _parse({"as_of": ["20260930"]}, as_of="20260909")
    assert ahead.stale_as_of == "20260930" and ahead.stale_is_ahead is True


def test_가려진_섹터는_쓰레기와_다르게_다뤄진다():
    """🔴 이 id 는 **우리 프레임에서 왔다.** 이름을 말해도 외부 글이 아니고,
    말해야 링크가 목적("이 섹터의 근거를 보라")을 잃지 않는다.
    """
    from dashboard import share

    parsed = _parse({"sector": ["robot"]})
    assert parsed.share.sector == "robot" and parsed.hidden_sector is None

    hidden = share.with_hidden_sector(parsed, frozenset({"steel"}))
    assert hidden.share.sector is None
    assert hidden.hidden_sector == "robot"
    # 🔒 `ignored` 에 들어가지 않는다 — 사유가 다르므로 화면이 다른 말을 한다
    assert "sector" not in hidden.ignored

    shown = share.with_hidden_sector(parsed, frozenset({"steel", "robot"}))
    assert shown.share.sector == "robot" and shown.hidden_sector is None


@pytest.mark.parametrize("built", [
    dict(profile="balanced"),
    dict(profile="momentum", hide_illiquid=True, hide_single_etf=True),
    dict(profile="balanced", custom=True, weights={"M": 70, "F": 60, "B": 40, "V": 30}),
    dict(profile="balanced", custom=True, weights={a: 0 for a in AXES}),
    dict(profile="momentum", gics=frozenset({"Materials", "Industrials"})),
    dict(profile="balanced", sector="steel", as_of="20260909"),
    dict(profile="momentum", custom=True, weights={"M": 1, "F": 0, "B": 0, "V": 100},
         gics=frozenset({"Materials"}), hide_illiquid=True, hide_single_etf=True,
         sector="robot", as_of="20260909"),
])
def test_링크는_왕복한다(built):
    """🔒 `encode` → `parse` 가 같은 상태를 돌려준다. 같은 화면은 같은 링크를 낸다."""
    from dashboard import share

    original = share.Share(**built)
    raw = {k: (v if isinstance(v, list) else [v])
           for k, v in share.encode(original).items()}
    back = _parse(raw)
    assert back.share == original, (back.share, original)
    assert back.ignored == () and back.unknown == 0


def test_gics_는_정렬해서_담는다():
    """🔒 같은 화면이 같은 링크를 내야 한다 — `frozenset` 의 순서는 보장이 없다."""
    from dashboard import share

    first = share.encode(share.Share(profile="balanced",
                                     gics=frozenset({"Industrials", "Materials"})))
    second = share.encode(share.Share(profile="balanced",
                                      gics=frozenset({"Materials", "Industrials"})))
    # 🔴 `first == second` 만으로는 **절대 실패할 수 없다** — 같은 프로세스에서 같은
    #    `frozenset` 은 반복 순서가 같아 `sorted` 를 지워도 통과한다(적대적 리뷰).
    #    일을 하는 것은 아래 리터럴 비교다
    assert first["gics"] == ["Industrials", "Materials"], first
    assert second["gics"] == ["Industrials", "Materials"], second


def test_기본값은_링크에_넣지_않고_프리셋은_항상_넣는다():
    """🔴 `profile` 을 빼면 "기본 프리셋" 이라는 뜻이 되고, 나중에 기본을 바꾸는 순간
    팀에 뿌린 옛 링크의 의미가 **조용히 변한다.**
    """
    from dashboard import share

    minimal = share.encode(share.Share(profile="balanced"))
    assert minimal == {"profile": "balanced"}


def test_퍼센트_인코딩이_붙는_글자를_쓰지_않는다():
    """🔒 `M:35,F:30` 이면 `urlencode` 가 `%3A`·`%2C` 로 바꿔 링크가 읽히지 않는다."""
    from urllib.parse import urlencode

    from dashboard import share

    encoded = share.encode(share.Share(profile="balanced", custom=True,
                                       weights={"M": 35, "F": 30, "B": 20, "V": 15}))
    assert urlencode({"w": encoded["w"]}) == "w=M35-F30-B20-V15"


# ── 공유 링크 — 실제 렌더 ───────────────────────────────────────────────────

def _gics_frame(mapping: dict[str, str], n_sectors: int = 3, *,
                diverging: bool = False) -> pd.DataFrame:
    out = frame(n_sectors, diverging=diverging)
    out["gics"] = [mapping[sid] for sid in out["sector_id"]]
    return out


def _app_with_link(monkeypatch, data_frame, **params):
    """쿼리 파라미터를 달고 앱을 띄운다.

    🔒 `at.query_params` 는 **인스턴스 속성**이고 run 뒤에는 그 스크립트가 남긴
       쿼리 문자열로 다시 채워진다 — 그래서 왕복을 그대로 관측할 수 있다
       (streamlit 1.63.0 실측).
    """
    from streamlit.testing.v1 import AppTest

    from dashboard import data as _data

    monkeypatch.setattr(_data, "_from_hf", lambda: None)
    monkeypatch.setattr(
        _data, "_from_local",
        lambda: (data_frame.copy(), _data.Source(kind="local", label="테스트용")))
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=120)
    for key, value in params.items():
        at.query_params[key] = value
    at.run()
    return at


def _warnings(at) -> str:
    """링크에 대한 경고만. 🔒 **면책을 뺀다** — 면책도 `st.warning` 으로 나온다
    (`theme.header`). 빼지 않으면 "아무 말도 하지 않는다" 를 단언할 수 없다.

    🔴 `!=` 로는 못 뺀다 — **`st.warning` 은 앞머리 이모지를 `icon` 으로 떼어 내서**
       `.value` 에 🔴 이 없다(streamlit 1.63.0 실측). 그래서 부분 문자열로 본다.
       같은 이유로 링크 경고의 ⏳🔎🔗 도 `.value` 에 없으니 **본문 글자로 단언한다.**
    """
    from dashboard import theme

    return "\n".join(w.value for w in at.warning if w.value not in theme.DISCLAIMER)


def test_링크로_열면_화면이_그_상태다(monkeypatch):
    at = _app_with_link(monkeypatch, frame(3), profile="momentum", illiquid="1",
                        single="1", sector="sec_2")
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert at.session_state["rank_profile"] == "momentum"
    assert at.session_state["rank_hide_illiquid"] is True
    assert at.session_state["rank_hide_single"] is True
    assert at.session_state["rank_detail"] == "sec_2"
    # 🔒 쓸 수 있었으므로 아무 말도 하지 않는다
    assert _warnings(at) == ""


def test_링크의_슬라이더_값이_그대로_열린다(monkeypatch):
    at = _app_with_link(monkeypatch, frame(3), profile="balanced", w="M70-F60-B40-V30")
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert at.session_state["rank_custom"] is True
    assert [at.session_state[f"rank_w_{a}"] for a in AXES] == [70, 60, 40, 30]


def test_반복된_gics_파라미터를_잃지_않는다(monkeypatch):
    """🔴 `dict(st.query_params)` 는 반복 파라미터를 **마지막 값으로 접는다.**

    🔒 그래서 `_raw_params` 가 `get_all` 로 모은다. 단일 값 링크만 테스트하면 그
       차이가 보이지 않아 `get_all` 을 버리는 돌연변이가 **살아남는다**(실제로 그랬다).
    """
    data_frame = _gics_frame({"sec_0": "Materials", "sec_1": "Industrials",
                              "sec_2": "Industrials"})
    at = _app_with_link(monkeypatch, data_frame, gics=["Materials", "Industrials"])
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert sorted(at.session_state["rank_gics"]) == ["Industrials", "Materials"]
    # 🔒 하나도 버리지 않았으므로 아무 말도 하지 않는다
    assert _warnings(at) == ""


def test_잘못된_링크가_화면을_죽이지_않고_키_이름만_말한다(monkeypatch):
    """🔴 `bind="query-params"` 를 기각한 이유가 이것이다 — 그쪽은 **조용히 지운다.**"""
    at = _app_with_link(monkeypatch, frame(3), profile=_PAYLOAD, w=_PAYLOAD,
                        illiquid="yes", sector=_PAYLOAD, **{_PAYLOAD: "x"})
    assert not at.exception, [str(e)[:300] for e in at.exception]
    # 🔒 표는 그려진다 — 링크가 나빠도 화면은 산다
    assert at.dataframe, "표가 없다"
    text = _warnings(at)
    assert "`profile`" in text and "`w`" in text and "`illiquid`" in text
    assert "모르는 파라미터 **1개**" in text
    # 🔴 **글자가 새지 않는다.** 화면 전체를 본다 — 경고만 보면 다른 칸으로 새는 것을 놓친다
    whole = "\n".join(
        [e.value for e in at.warning] + [e.value for e in at.markdown]
        + [e.value for e in at.error] + [e.value for e in at.info]
        + [e.value for e in at.caption] + [str(s.options) for s in at.selectbox]
    )
    assert _PAYLOAD not in whole and "script" not in whole


def test_링크가_가리킨_섹터가_필터에_가려지면_이름을_말한다(monkeypatch):
    """🔒 쓰레기와 **다른 사유**다 — 그 id 는 우리 프레임에서 왔다."""
    data_frame = frame(3)
    # 🔒 sec_0 만 ETF 1종으로 만들어 '1종 숨기기' 로 가린다
    data_frame.loc[data_frame["sector_id"] == "sec_0", "etf_n"] = 1
    at = _app_with_link(monkeypatch, data_frame, sector="sec_0", single="1")
    assert not at.exception, [str(e)[:300] for e in at.exception]
    text = _warnings(at)
    assert "sec_0" in text, text
    assert "필터를 풀면" in text
    # 🔒 `ignored` 사유로 섞이지 않는다 — 그러면 "기본값으로 열었다" 라고 잘못 말한다
    assert "`sector`" not in text, text


def test_링크의_기준일이_지나면_화면이_말한다(monkeypatch):
    at = _app_with_link(monkeypatch, frame(3), as_of="20260101")
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert "20260101" in _warnings(at)


def test_URL_은_첫_로드에만_이긴다(monkeypatch):
    """🔴 매 rerun 적용하면 링크로 들어온 사람이 **아무것도 바꿀 수 없다.**

    🔒 검증에 **라디오**를 쓴다 — `profile` 은 `encode` 가 언제나 넣는 키라
       "URL 이 말한 값" 과 "위젯이 바꾼 값" 이 정면으로 부딪친다. URL 에 없던 키로
       쓰면 플래그를 지워도 테스트가 통과한다(적대적 설계 리뷰).
    🔒 같은 세션에 **다른 링크**를 붙여 보는 방식은 쓰지 않는다 — 같은 페이지 rerun 에서
       백엔드는 URL 을 다시 읽지 않아(`script_runner`) 항상 통과하는 허위 테스트가 된다.
    """
    at = _app_with_link(monkeypatch, frame(3), profile="momentum")
    assert at.session_state["rank_profile"] == "momentum"

    other = next(p for p in view.PROFILES if p != "momentum")
    at.radio(key="rank_profile").set_value(other).run()
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert at.session_state["rank_profile"] == other, "URL 이 위젯을 다시 이겼다"


@pytest.mark.parametrize("params", [
    {"profile": "momentum", "sector": "sec_9"},      # 프레임에 없는 섹터
    {"profile": "momentum", "gics": "없는대분류"},
    {"profile": "momentum", "as_of": "2026-09-01"},
    {"profile": "momentum", "sector": "sec_0", "single": "1"},  # 필터에 가려진 섹터
])
def test_무시가_생긴_링크로도_위젯을_바꿀_수_있다(monkeypatch, params):
    """🔴 이것이 **가장 나쁜 버그**였다 — `?sector=sec_9` 한 줄이 그 세션의 모든 위젯을
    영구히 잠갔다. 오타 하나, 슬랙에서 잘린 링크 하나로 충분했다.

    원인은 "프레임 때문에 무시된 것이 있으면 플래그를 세우지 않는다" 였다(드문 경우를
    지키려던 조건). 플래그가 없으니 **매 rerun URL 이 위젯을 다시 이겼다.**
    🔒 이제 플래그는 무조건 선다 (`_apply_link` 머리주석).

    🔒 기존 `test_URL_은_첫_로드에만_이긴다` 는 `profile` 하나짜리 링크로만 검증해서
       **무시가 생기는 분기를 한 건도 밟지 않았다.** 그래서 이 표가 따로 있다.
    """
    data_frame = frame(3)
    data_frame.loc[data_frame["sector_id"] == "sec_0", "etf_n"] = 1
    at = _app_with_link(monkeypatch, data_frame, **params)
    assert not at.exception, [str(e)[:300] for e in at.exception]

    other = next(p for p in view.PROFILES if p != "momentum")
    at.radio(key="rank_profile").set_value(other).run()
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert at.session_state["rank_profile"] == other, "URL 이 라디오를 다시 이겼다"

    # 🔒 필터도 만질 수 있어야 한다 — `hidden_sector` 경로는 "필터를 풀면 나온다" 고
    #    말하면서 매 rerun 필터를 다시 켰다. 그 지시가 불가능한 행동이었다
    at.checkbox(key="rank_hide_single").set_value(False).run()
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert at.session_state["rank_hide_single"] is False, "URL 이 체크박스를 다시 이겼다"


@pytest.mark.parametrize("value", _NASTY)
def test_나쁜_값이_어느_키에_와도_화면이_산다(monkeypatch, value):
    """🔴 `?w=M²-F30-B20-V15` 가 실제로 표를 0개로 만들었다 — 적용이 위젯보다 앞이라
    파싱 예외가 **페이지 전체**를 가린다.
    """
    at = _app_with_link(monkeypatch, frame(3), profile=value, w=value, gics=value,
                        illiquid=value, single=value, sector=value, as_of=value)
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert at.dataframe, "표가 없다 — 링크 한 줄이 화면을 가렸다"


def test_화면의_대분류_분포가_필터를_따른다(monkeypatch):
    """🔴 순수 함수 테스트로는 **렌더러가 `only=keep` 을 넘기는지 볼 수 없다** —
    `only=` 를 지우는 돌연변이가 살아남았다(돌연변이 검사). 그리는 것을 본다.

    🔒 필터가 숨긴 대분류가 이 칸에 남으면 "필터는 행을 숨긴다"(ADR-SC-0014 ⑤)를
       한 화면의 한 칸이 따르지 않는 것이고, 비개발자에게 캡션으로 설명될 차이가 아니다.
    """
    data_frame = _gics_frame({"sec_0": "Materials", "sec_1": "Industrials",
                              "sec_2": "Industrials"})
    at = _app_with_link(monkeypatch, data_frame, gics="Industrials")
    assert not at.exception, [str(e)[:300] for e in at.exception]

    # 🔒 표의 색인은 **한국어 이름**이다 (`Names.gics_label`) — 화면이 코드를 보이지 않는다
    from dashboard import data as _data

    names = _data.sector_names()
    kept, hidden = names.gics_label("Industrials"), names.gics_label("Materials")
    assert kept != "Industrials" and hidden != "Materials", (kept, hidden)

    shown = [set(str(v) for v in d.value.index) for d in at.dataframe]
    assert any(kept in s for s in shown), (kept, shown)
    # 🔴 걸러진 대분류가 **어느 표에도** 없다 — 표 번호에 기대지 않는다
    assert not any(hidden in s for s in shown), (hidden, shown)


def test_버튼을_누르지_않으면_주소창을_건드리지_않는다(monkeypatch):
    """🔴 `st.query_params` 쓰기는 프런트엔드에서 `history.pushState` 가 된다.

    매 rerun 자동 갱신은 클릭마다 히스토리를 쌓아 **뒤로가기로 앱을 떠날 수 없게**
    만든다. 그래서 누를 때만 쓴다.
    """
    at = _app_with_link(monkeypatch, frame(3), profile="momentum")
    before = dict(at.query_params)

    at.radio(key="rank_profile").set_value("balanced").run()
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert dict(at.query_params) == before, "버튼 없이 주소창이 바뀌었다"


def test_버튼을_누르면_주소창이_화면과_같아진다(monkeypatch):
    from dashboard import share

    at = _app_with_link(monkeypatch, frame(3))
    at.radio(key="rank_profile").set_value("momentum").run()
    at.checkbox(key="rank_hide_single").set_value(True).run()
    assert not at.exception, [str(e)[:300] for e in at.exception]

    at.button(key="rank_share").click().run()
    assert not at.exception, [str(e)[:300] for e in at.exception]

    got = {k: (v if len(v) > 1 else v[0]) for k, v in at.query_params.items()}
    expected = share.encode(share.Share(
        profile="momentum", hide_single_etf=True,
        sector=at.session_state["rank_detail"], as_of=DAYS[-1]))
    assert got == expected, (got, expected)
    assert any("주소창" in s.value for s in at.success), [s.value for s in at.success]


def test_버튼이_화면의_슬라이더를_담는다_전부_0_이어도(monkeypatch):
    """🔴 네 축이 전부 0 이면 화면은 오류를 내고 **프리셋으로 그린다.**

    그때 `Weighting.weights` 는 프리셋 값이라 화면의 슬라이더와 다르다. 링크는
    화면을 재현해야 하므로 **세션값**을 담는다.
    """
    at = _app_with_link(monkeypatch, frame(3), profile="balanced", w="M0-F0-B0-V0")
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert at.session_state["rank_custom"] is True

    at.button(key="rank_share").click().run()
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert at.query_params["w"] == ["M0-F0-B0-V0"], at.query_params


def test_필터로_아무것도_안_남는_화면도_공유된다(monkeypatch):
    """🔒 "내 필터로는 아무것도 안 남는다" 도 사실이고 공유할 만하다.

    🔴 그 화면에는 selectbox 가 **없다.** 그래서 `sector=None` 을 넘긴다 — 그 run 이
       그린 값이 없기 때문이다.

    ⚠️ **이 테스트는 "세션에서 읽어 오는" 돌연변이를 잡지 못한다** — 실측상 그 run 에서는
       `rank_detail` 이 `session_state` 에서 사라져 두 코드의 결과가 같다(돌연변이 검사로
       확인). 무력한 단언을 남겨 두는 대신 **그 전제를 직접 단언한다**: 세션값이 남게
       바뀌면 여기가 먼저 깨지고, 그때 `sector=None` 이 비로소 값을 하는 코드가 된다.
    """
    data_frame = frame(3)
    data_frame["etf_n"] = 1
    at = _app_with_link(monkeypatch, data_frame, sector="sec_1")
    assert at.session_state["rank_detail"] == "sec_1"

    at.checkbox(key="rank_hide_single").set_value(True).run()
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert not at.selectbox, "빈 화면에 셀렉트박스가 있다"
    # 🔒 이것이 위에서 말한 전제다. 실측 사실을 테스트가 붙잡는다
    assert "rank_detail" not in at.session_state, (
        "그리지 않은 selectbox 의 세션값이 남는다 — `_share_button(sector=None)` 이 "
        "이제 값을 하는 코드다. 빈 화면 경로를 다시 본다")

    at.button(key="rank_share_empty").click().run()
    assert not at.exception, [str(e)[:300] for e in at.exception]
    assert "sector" not in at.query_params, at.query_params


def test_점수_없는_섹터가_있어도_공유_버튼이_산다(monkeypatch):
    """🔒 이슈 #3 의 결측 프레임과 M9c 가 겹치는 자리를 함께 밟는다."""
    at = _app_with_link(monkeypatch, blank_latest(3, blanks=1), profile="balanced")
    assert not at.exception, [str(e)[:300] for e in at.exception]
    at.button(key="rank_share").click().run()
    assert not at.exception, [str(e)[:300] for e in at.exception]


# ── 대분류 순위 분포 (M9c) ──────────────────────────────────────────────────

def test_대분류_분포는_순위_목록을_그대로_준다():
    table = view.gics_distribution(
        view.scored(_gics_frame({"sec_0": "Materials", "sec_1": "Materials",
                                 "sec_2": "Industrials"}), BALANCED))
    # 🔒 순서를 **단정한다.** `or` 로 두 순열을 다 허용하면 아무것도 고정되지 않는다
    #: 🔒 픽스처는 `base = (s-1)*5000` 이라 **sec_2 가 1위**다 — Industrials 가 앞이다
    assert list(table.index) == ["Industrials", "Materials"], list(table.index)
    row = table.loc["Materials"]
    assert row["테마수"] == 2
    assert row["순위"].count("·") == 1, row["순위"]
    assert row["순위없음"] == 0


def test_대분류_분포에_평균도_중위도_없다():
    """🔴 요약값은 정보를 늘리지 않으면서 **점수처럼 읽힌다** — 금지한 롤업으로 가는 길이다.

    🔒 목록이 곧 분포다 (ADR-SC-0014 ⑤).
    """
    table = view.gics_distribution(scored(3))
    assert set(table.columns) == {"테마수", "최고순위", "순위", "순위없음"}


def test_대분류_분포는_최고순위로_정렬한다():
    """🔒 정렬 정본은 **정수 열**이다. 목록 문자열로 정렬하면 사전순이 되어 10 이 2 앞에 온다."""
    table = view.gics_distribution(
        view.scored(_gics_frame({"sec_0": "A", "sec_1": "B", "sec_2": "C"}), BALANCED))
    best = [view.int_or_none(v) for v in table["최고순위"]]
    assert best == sorted(best), best
    # 🔒 정수 열이어야 화면이 숫자로 정렬한다
    assert str(table["최고순위"].dtype) == "Int64", table["최고순위"].dtype


def test_순위_없는_섹터를_최악_순위로_취급하지_않는다():
    """🔴 `None` 은 "창이 안 찼다" 이고 21위가 아니다 (ADR-SC-0007 · 이슈 #3)."""
    blank = blank_latest(3, blanks=1)
    blank["gics"] = ["Materials" if sid == "sec_0" else "Industrials"
                     for sid in blank["sector_id"]]
    table = view.gics_distribution(view.scored(blank, BALANCED))

    materials = table.loc["Materials"]
    assert materials["테마수"] == 1
    assert materials["순위없음"] == 1
    assert materials["순위"] == "", materials["순위"]
    # 🔒 최고순위가 **없음**이다 — 0 도 999 도 아니다
    assert view.int_or_none(materials["최고순위"]) is None
    # 🔒 그리고 맨 뒤로 간다
    assert list(table.index)[-1] == "Materials", list(table.index)


def test_대분류_분포는_필터를_따른다():
    """🔒 표·등수·막대와 **같은 규칙**이다. 🔴 그래도 순위 자체는 21개 횡단면 값이다."""
    data_frame = _gics_frame({"sec_0": "Materials", "sec_1": "Materials",
                              "sec_2": "Industrials"})
    ranked = view.scored(data_frame, BALANCED)
    only = view.gics_distribution(ranked, only=frozenset({"sec_2"}))
    assert list(only.index) == ["Industrials"]

    # 🔴 숨겼어도 남은 섹터의 순위는 **다시 매겨지지 않는다**
    whole = view.gics_distribution(ranked)
    assert only.loc["Industrials", "순위"] == whole.loc["Industrials", "순위"]


def test_대분류_분포는_가중치를_따른다():
    """🔴 프리셋으로 못 박는 돌연변이가 전 스위트를 통과했다 — 그러면 슬라이더를 만진
    화면에서 표·막대는 커스텀 순위, 분포는 프리셋 순위를 말한다. ADR-SC-0014 ④ 가
    금지한 "한 화면의 두 숫자" 다.
    """
    # 🔒 `diverging=True` 여야 한다 — 기본 픽스처는 네 축이 섹터를 같은 순서로 세워
    #    어떤 가중치로도 순위가 같다(`frame` 머리주석)
    data_frame = _gics_frame({"sec_0": "Materials", "sec_1": "Industrials",
                              "sec_2": "Utilities"}, diverging=True)
    # 🔒 V 축만 쓰는 가중치 — 균형과 순위가 갈리도록 고른다
    custom = weights.Weighting.of({"M": 0, "F": 0, "B": 0, "V": 100})
    ranked = view.scored(data_frame, custom)
    table = view.gics_distribution(ranked)
    expected = view.ranking_table(ranked, days=5)

    for sid, gics in (("sec_0", "Materials"), ("sec_1", "Industrials"),
                      ("sec_2", "Utilities")):
        assert (view.int_or_none(table.loc[gics, "최고순위"])
                == view.int_or_none(expected.loc[sid, "순위"])), (sid, gics)

    # 🔒 그리고 균형과 **실제로 다르다** — 다르지 않으면 이 테스트가 무력하다.
    #    🔴 `list(table["최고순위"])` 로 비교하면 안 된다 — 표가 그 열로 정렬돼 있어
    #    **언제나 `[1,2,3]`** 이다. 어느 대분류가 몇 위인가를 봐야 한다
    balanced = view.gics_distribution(view.scored(data_frame, BALANCED))
    assert table["최고순위"].to_dict() != balanced["최고순위"].to_dict(), (
        "픽스처가 가중치를 안 가른다 — `diverging=True` 인지 확인한다")


def test_대분류가_없는_섹터도_표에_남는다():
    """🔴 pandas 기본 `dropna=True` 가 그 섹터를 **조용히 버렸다** — 3개 중 2개만
    세어졌다. 값이 없는 것도 사실이므로 `미분류` 로 드러낸다 (ADR-SC-0007).

    🔒 `""` 와 `None` 을 **한 줄로** 묶는다. 각각 그룹이 되면 색인이 중복돼
       `table.loc["미분류"]` 가 Series 가 아니라 DataFrame 이 된다.
    """
    data_frame = frame(3)
    data_frame["gics"] = ["Materials", "", None] * (len(data_frame) // 3)
    table = view.gics_distribution(view.scored(data_frame, BALANCED))

    assert int(table["테마수"].sum()) == 3, table
    assert not table.index.duplicated().any(), list(table.index)
    assert view.UNCLASSIFIED in table.index
    assert int(table.loc[view.UNCLASSIFIED, "테마수"]) == 2


def test_대분류_이름이_겹치면_검증기가_막는다():
    """🔒 분포표는 **화면 이름으로** 묶어 색인을 만든다. 서로 다른 두 id 가 같은 한국어
    이름을 가지면 두 대분류가 한 줄로 합쳐지고 사용자는 합쳐진 줄 알 수 없다 —
    그래서 근원에서 막는다 (`sector_master._check_ids`).
    """
    from sector import sector_master

    findings = [f for f in sector_master.validate(sector_master.load())
                if f.rule == "name-unique"]
    assert findings == [], findings
    # 🔒 규칙이 **실제로 대분류를 본다** — 섹터만 보던 검사였다
    source = (ROOT / "sector" / "sector_master.py").read_text(encoding="utf-8")
    assert "gics_sectors" in source.split("def _check_ids", 1)[1].split("\ndef ", 1)[0]


def test_대분류_분포는_빈_집합에서_죽지_않는다():
    assert len(view.gics_distribution(scored(3), only=frozenset())) == 0


# ── 축수 — 표가 점수를 설명한다 (이슈 #4) ────────────────────────────────────

def test_커스텀에서_축수가_점수를_설명한다():
    """🔴 이슈 #4 의 재현. M 만 가중하면 `점수 == M` 인데 표는 **축수 4** 라고 말했다.

    저장 열 `n_axes_used` 는 `z` 가 있는 축을 셀 뿐 **가중치를 보지 않기** 때문이다.
    """
    only_m = weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0})
    out = view.scored(frame(), only_m)

    assert list(out[view.SCORE_COLUMN]) == list(out["m_z_bp"]), "점수가 M 축 그 자체가 아니다"
    assert set(out[view.SCORE_AXES_COLUMN]) == {1}
    # 🔒 저장 열은 **덮지 않는다** (ADR-SC-0014 ③) — 에이전트 guard 가 원천으로 읽는다
    assert set(out["n_axes_used"]) == {4}


def test_표의_축수_칸이_새_열을_읽는다():
    """🔴 `ranking_table` 이 저장 열을 그리면 이슈 #4 가 표에 그대로 남는다."""
    only_m = weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0})
    assert set(view.ranking_table(view.scored(frame(), only_m))["축수"]) == {1}
    # 프리셋은 네 축을 다 쓰므로 4 다
    assert set(view.ranking_table(scored())["축수"]) == {4}


def test_프리셋에서는_축수가_저장_열과_같다():
    """🔒 프리셋 셋은 네 축이 전부 0 보다 크다(`test_프리셋에는_가중치_0_인_축이_없다`).

    그래서 다시 세도 저장 열과 같은 답이 나와야 한다 — 다르면 둘 중 하나가 틀렸다.
    """
    data = frame(4)
    data["v_z_bp"] = data["v_z_bp"].astype("Int64")
    data["n_axes_used"] = data["n_axes_used"].astype("Int64")
    # 🔴 결측이 없으면 두 값이 우연히 같아 테스트가 무력하다 — 배치가 적는 모양대로 심는다
    blank = data["sector_id"] == "sec_0"
    data.loc[blank, "v_z_bp"] = pd.NA
    data.loc[blank, "n_axes_used"] = 3

    for name in PRESETS:
        out = view.scored(data, weights.Weighting.preset(name))
        assert set(out[view.SCORE_AXES_COLUMN]) == {3, 4}, "결측 행이 안 섞였다"
        assert list(out[view.SCORE_AXES_COLUMN]) == list(out["n_axes_used"]), name


def test_축수는_scoring_axes_와_한_글자도_다르지_않다():
    """🔒 `view._axes_used_n` 은 속도 때문에 **열 단위**로 세고(이슈 #1), 규칙의 정본은
    `sector.scoring.scoring_axes` 다. 두 구현이 갈라지지 않는지 행마다 대조한다.

    🔴 축을 **골고루** 비운다 — 결측이 없으면 어떤 잘못된 구현도 통과한다.
    """
    data = frame(4)
    for axis in AXES:
        column = f"{axis.lower()}_z_bp"
        data[column] = data[column].astype("Int64")
    for i, axis in enumerate(AXES):
        data.loc[data["sector_id"] == f"sec_{i}", f"{axis.lower()}_z_bp"] = pd.NA

    for weighting in (BALANCED,
                      weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0}),
                      weights.Weighting.of({"M": 0, "F": 1, "B": 0, "V": 2}),
                      weights.Weighting.of({"M": 0, "F": 0, "B": 0, "V": 7})):
        out = view.scored(data, weighting)
        for row in out.itertuples(index=False):
            z_bp = {"M": row.m_z_bp, "F": row.f_z_bp, "B": row.b_z_bp, "V": row.v_z_bp}
            # 🔒 `pd.NA` 는 `is not None` 이 참이다 — 정본 함수에 넘기기 전에 `None` 으로
            plain = {a: (None if pd.isna(v) else int(v)) for a, v in z_bp.items()}
            expected = scoring_axes(plain, weighting.weights)
            assert row.score_axes_n == len(expected), (row.sector_id, weighting.weights)
            # 🔒 축수가 0 인 것과 점수가 없는 것은 **같은 사건**이어야 한다.
            #    `np.int64 == 0` 은 `np.False_` 라 `is` 로 비교되지 않는다
            assert bool(row.score_axes_n == 0) == bool(pd.isna(row.score_bp))


def test_점수가_없으면_축수가_0_이고_결측이_아니다():
    """🔒 축수는 **셀 수 있는 것**이라 0 이 정직하다 — `—` 로 비우면 '모른다' 가 된다.

    점수 쪽은 반대다. 점수 0 은 "중립적으로 평가됐다" 는 뜻이 되므로 **없음**으로 남긴다
    (ADR-SC-0007).
    """
    data = frame()
    data["m_z_bp"] = data["m_z_bp"].astype("Int64")
    data.loc[data["sector_id"] == "sec_0", "m_z_bp"] = pd.NA
    only_m = weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0})

    out = view.scored(data, only_m)
    assert str(out[view.SCORE_AXES_COLUMN].dtype) == "Int64"
    row = out[out["sector_id"] == "sec_0"]
    assert len(row) == len(DAYS), "픽스처가 바뀌었다 — sec_0 은 날마다 한 행이다"
    assert row[view.SCORE_COLUMN].isna().all()
    assert set(row[view.SCORE_AXES_COLUMN]) == {0}


def test_축수도_색인이_중복된_프레임에서_밀리지_않는다():
    """🔒 점수 열이 실제로 그랬다(2026-09-17) — 라벨로 맞추면 한 값이 여러 행으로 퍼진다."""
    plain = frame(4)
    plain["m_z_bp"] = plain["m_z_bp"].astype("Int64")
    plain.loc[plain["sector_id"] == "sec_0", "m_z_bp"] = pd.NA
    duplicated = plain.copy()
    duplicated.index = [0] * len(duplicated)

    only_m = weights.Weighting.of({"M": 100, "F": 0, "B": 0, "V": 0})
    expected = list(view.scored(plain, only_m)[view.SCORE_AXES_COLUMN])
    assert set(expected) == {0, 1}, "픽스처가 두 값을 만들지 못했다 — 테스트가 무력하다"
    assert list(view.scored(duplicated, only_m)[view.SCORE_AXES_COLUMN]) == expected
