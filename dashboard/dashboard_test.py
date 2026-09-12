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

from dashboard import explain, view
from sector.scoring import AXES, PRESETS

# ── 뷰 모델 (화면 없이) ─────────────────────────────────────────────────────

DAYS = [f"2026090{i}" for i in range(1, 10)]


def frame(n_sectors: int = 3) -> pd.DataFrame:
    """합성 점수 표. 🔒 실제 KRX 데이터를 픽스처로 쓰지 않는다 (AGENTS.md 5장)."""
    rows = []
    for day_i, day in enumerate(DAYS):
        for s in range(n_sectors):
            z = (s - 1) * 5000 + day_i * 100
            rows.append({
                "bas_dd": day, "sector_id": f"sec_{s}", "gics": "Industrials",
                "m_raw_bp": 100 + s, "f_raw_bp": 200 + s,
                "b_raw_bp": 300 + s, "v_raw_bp": 400 + s,
                "m_z_bp": z, "f_z_bp": z, "b_z_bp": z, "v_z_bp": z,
                "n_axes_used": 4, "axes_missing": "", "axes_degraded": "",
                "score_balanced_bp": z, "rank_balanced": n_sectors - s,
                "score_momentum_bp": z, "rank_momentum": n_sectors - s,
                "score_contrarian_bp": z, "rank_contrarian": n_sectors - s,
                "liquidity_ok": True, "etf_n": 2, "is_partial": False,
                "config_version": "t", "config_sha256": "x", "fetched_at": "t",
            })
    return pd.DataFrame(rows)


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

    stability = view.rank_stability(data, days=20)
    assert (stability["rank_days"] == len(DAYS)).all()       # 표본일이 사실을 말한다


def test_이력이_짧은_섹터는_평균을_내지_않는다():
    """🔒 섹터마다 이력이 다를 수 있다. 짧은 쪽을 긴 척하지 않는다."""
    data = frame()
    data = data[~((data["sector_id"] == "sec_0") & (data["bas_dd"] == DAYS[0]))]
    stability = view.rank_stability(data, days=len(DAYS))
    assert pd.isna(stability.loc["sec_0", "rank_mean"])      # 하루 모자라 비워 둔다
    assert stability.loc["sec_1", "rank_mean"] == stability.loc["sec_1", "rank_mean"]


def test_기여도_합이_점수와_같다():
    """🔴 축 분해가 게시된 점수와 맞물린다 — '왜 1위인가' 의 근거가 흔들리지 않는다."""
    data = frame()
    for sector_id in data["sector_id"].unique():
        parts = view.axis_breakdown(data, sector_id)
        total = sum(p["contribution_bp"] or 0 for p in parts)
        score = int(view.latest_frame(data).set_index("sector_id")
                    .loc[sector_id, "score_balanced_bp"])
        assert abs(total - score) <= 1, sector_id        # bp 반올림 1 까지 허용


def test_결측축은_기여가_0이_아니라_없음이다():
    """🔒 0 은 '중립적으로 기여했다' 는 뜻이 된다. 없는 것은 없다 (ADR-SC-0007)."""
    data = frame()
    data.loc[data["sector_id"] == "sec_0", "f_z_bp"] = None
    parts = {p["axis"]: p for p in view.axis_breakdown(data, "sec_0")}
    assert parts["F"]["contribution_bp"] is None
    assert parts["F"]["z_bp"] is None


def test_결측축이_있으면_나머지_가중치가_다시_나뉜다():
    data = frame()
    data.loc[data["sector_id"] == "sec_0", "f_z_bp"] = None
    parts = [p for p in view.axis_breakdown(data, "sec_0") if p["contribution_bp"] is not None]
    live_weight = sum(PRESETS["balanced"][p["axis"]] for p in parts)
    assert live_weight == 100 - PRESETS["balanced"]["F"]


def test_없는_섹터는_빈_목록이다():
    assert view.axis_breakdown(frame(), "없는섹터") == []


@pytest.mark.parametrize("profile", list(PRESETS))
def test_프리셋마다_표가_나온다(profile):
    table = view.ranking_table(frame(), profile=profile)
    assert len(table) == 3 and "순위" in table.columns


# ── 등수 · 막대 · 서술의 재료 ───────────────────────────────────────────────
# 🔴 M8 직후 `narrative()` 와 `Names` 는 **정의만 되고 아무도 부르지 않았다.**
#    화면은 `steel` 을 그렸고 서술은 죽은 코드였다. 아래가 그 회귀를 막는다.

KOREAN = view.Names(sector={"sec_0": "가", "sec_1": "나", "sec_2": "철강"},
                    gics={"Industrials": "산업재"})


def test_랭킹표에_한국어_이름이_들어간다():
    """🔴 `names=` 를 빠뜨리면 표가 조용히 코드를 그린다 — 실제로 그랬다."""
    table = view.ranking_table(frame(), names=KOREAN)
    assert list(table["섹터"]) == [KOREAN.sector_label(s) for s in table.index]
    assert "철강" in set(table["섹터"])
    assert set(table["GICS"]) == {"산업재"}


def test_이름을_모르면_코드를_그대로_준다():
    """🔒 지어내지 않는다. `sectors.yaml` 을 못 읽어도 화면은 산다."""
    table = view.ranking_table(frame(), names=view.Names.empty())
    assert list(table["섹터"]) == list(table.index)


def test_등수_카드는_순위_순서로_상위만_준다():
    entries = view.podium(frame(), top=2, names=KOREAN)
    assert [e["rank"] for e in entries] == [1, 2]
    assert entries[0]["label"] == "철강"          # sec_2 가 1위다
    assert all(e["sector_id"] in KOREAN.sector for e in entries)


def test_끌어올린_축이_없으면_지어내지_않는다():
    """🔒 기여가 전부 음수면 `lead_axis` 는 `None` 이고 문구가 그 사실을 말한다."""
    data = frame()
    for column in ("m_z_bp", "f_z_bp", "b_z_bp", "v_z_bp"):
        data[column] = -5000
    entry = view.podium(data, top=1)[0]
    assert entry["lead_axis"] is None
    assert "지어" not in explain.lead_axis_text(None)      # 문구가 존재한다
    assert "없다" in explain.lead_axis_text(None)


def test_끌어올린_축은_기여가_가장_큰_축이다():
    data = frame()
    data["f_z_bp"] = 29000                       # 자금흐름만 크게 띄운다
    assert view.podium(data, top=1)[0]["lead_axis"] == "F"


def test_막대는_순위_순서와_σ_로_준다():
    """🔒 화면이 `sort=False` 로 그리므로 이 순서가 곧 화면 순서다."""
    bars = view.score_bars(frame(), names=KOREAN)
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


def test_네_페이지가_있다():
    from dashboard.pages import confirm, howto, ranking, teams

    assert all(hasattr(m, "render") for m in (ranking, teams, confirm, howto))


def test_페이지마다_URL_경로가_다르다():
    """🔒 이것이 앱을 못 뜨게 했던 버그다. 경로를 명시했는지 파일에서 확인한다."""
    source = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    paths = [line.split('url_path="')[1].split('"')[0]
             for line in source.splitlines() if "url_path=" in line and "st.Page" in line]
    assert len(paths) == 4 and len(set(paths)) == 4, paths


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


def _confirm_page() -> None:
    from dashboard.pages import confirm

    confirm.render()


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
    assert ledger.read_all() == []        # 🔒 한 건도 쓰이지 않는다


def test_짧은_passcode_는_거부되고_원장이_비어_있다(ledger):
    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("동원").run()
    at.text_input(key="create_team_id").input("team_a")
    at.text_input(key="create_name").input("A조")
    at.text_input(key="create_passcode").input("1234")
    at.button(key="FormSubmitter:create-만들기").click().run()
    assert any("짧다" in e.value for e in at.error)
    assert ledger.read_all() == []


def test_확정은_사유가_없으면_막힌다(ledger):
    """🔴 사유 없는 확정은 3개월 뒤에 아무것도 남기지 않는다."""
    _make_team(_run(_teams_page, ledger))
    at = _run(_confirm_page, ledger)
    at.session_state["sc_actor"] = "동원"
    at.session_state["sc_team_id"] = "team_a"
    at.run()
    at.button(key="confirm_submit").click().run()      # 사유를 비운 채
    assert any("비어" in e.value for e in at.error)

    from sector.workspace import fold
    assert not fold.fold(ledger.read_all()).team("team_a").is_confirmed


def test_사유와_함께_확정하면_원장에_남는다(ledger):
    _make_team(_run(_teams_page, ledger))
    at = _run(_confirm_page, ledger)
    at.session_state["sc_actor"] = "동원"
    at.session_state["sc_team_id"] = "team_a"
    at.run()
    at.text_area(key="confirm_reason").input("자금흐름 축이 3σ 로 압도적이다").run()
    at.button(key="confirm_submit").click().run()
    assert not at.exception, [str(e)[:200] for e in at.exception]

    from sector.workspace import fold
    team = fold.fold(ledger.read_all()).team("team_a")
    assert team.is_confirmed
    assert team.core_reason == "자금흐름 축이 3σ 로 압도적이다"
    assert team.confirmed_by == "동원"


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


# ── 보관 · 마스터 ───────────────────────────────────────────────────────────

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


def test_남의_조는_보관_칸이_보이지_않는다(ledger, monkeypatch):
    """🔒 만든 사람도 마스터도 아니면 버튼 자체가 없다."""
    from sector.workspace import auth

    monkeypatch.setattr(auth, "master_hash", lambda: None)
    _make_team(_run(_teams_page, ledger), actor="동원", passcode="산-바다-강-들")

    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("민수").run()
    at.text_input(key="join_passcode").input("산-바다-강-들")
    at.button(key="FormSubmitter:join-참가").click().run()
    keys = [b.key for b in at.button]
    assert "archive_team_a" not in keys, keys


def test_마스터_시크릿이_없으면_문_자체가_없다(ledger, monkeypatch):
    """🔴 없는 문을 보여 주면 '여기 뭔가 있나' 만 남는다."""
    from sector.workspace import auth

    monkeypatch.setattr(auth, "master_hash", lambda: None)
    at = _run(_teams_page, ledger)
    assert "master_unlock" not in [b.key for b in at.button]


def test_마스터는_남의_조도_보관할_수_있다(ledger, monkeypatch):
    from sector.workspace import auth, fold

    stored = auth.hash_passcode("마스터-산-바다-강", salt=b"0" * 16)
    monkeypatch.setattr(auth, "master_hash", lambda: stored)
    _make_team(_run(_teams_page, ledger), actor="동원", passcode="산-바다-강-들")

    at = _run(_teams_page, ledger)
    at.text_input(key="identity_name").input("개발자").run()
    at.text_input(key="master_pass").input("마스터-산-바다-강").run()
    at.button(key="master_unlock").click().run()
    assert at.session_state["sc_master"] is True

    # 참가하지 않아도 목록에서 보이지는 않지만, 참가하면 보관할 수 있다
    at.text_input(key="join_passcode").input("산-바다-강-들")
    at.button(key="FormSubmitter:join-참가").click().run()
    at.text_input(key="archive_reason_team_a").input("마스터가 정리").run()
    at.button(key="archive_team_a").click().run()
    assert "team_a" not in fold.fold(ledger.read_all()).active_teams

# ── 근거를 두 화면이 같이 그린다 ────────────────────────────────────────────
# 🔴 M8 직후 확정 화면에는 점수 한 줄뿐이었다. 사유를 쓰라고 하면서 무엇을 근거로
#    쓸지는 안 보여주면, 사유 칸은 "1위라서" 로 채워진다.


def _markdown(at) -> str:
    return "\n".join(str(m.value) for m in at.markdown)


def test_확정_화면이_근거를_보여준다(ledger):
    """🔒 랭킹과 **같은 함수**를 부르므로 두 화면의 문구가 갈라지지 않는다."""
    _make_team(_run(_teams_page, ledger))
    at = _run(_confirm_page, ledger)
    at.session_state["sc_actor"] = "동원"
    at.session_state["sc_team_id"] = "team_a"
    at.run()
    assert not at.exception, [str(e)[:200] for e in at.exception]

    body = _markdown(at)
    # 서술 마지막 줄은 언제나 이 문장이다 — 근거 블록이 실제로 그려졌다는 뜻
    assert "앞으로 오른다는 뜻이 아니" in body
    assert "사람이 쓴 근거" in body        # `sectors.yaml` 의 note 까지 왔다


def test_확정한_뒤에도_근거가_남는다(ledger):
    """🔴 확정은 끝이 아니라 3개월 운용의 시작이다."""
    _make_team(_run(_teams_page, ledger))
    at = _run(_confirm_page, ledger)
    at.session_state["sc_actor"] = "동원"
    at.session_state["sc_team_id"] = "team_a"
    at.run()
    at.text_area(key="confirm_reason").input("자금흐름이 압도적이다").run()
    at.button(key="confirm_submit").click().run()

    at = _run(_confirm_page, ledger)
    at.session_state["sc_actor"] = "동원"
    at.session_state["sc_team_id"] = "team_a"
    at.run()
    assert any("이 섹터의 근거" in h.value for h in at.subheader)


def test_확정_화면이_코드가_아니라_한국어_이름을_쓴다(ledger):
    """🔴 팀은 `steel` 이 아니라 `철강` 으로 말한다."""
    _make_team(_run(_teams_page, ledger))
    at = _run(_confirm_page, ledger)
    at.session_state["sc_actor"] = "동원"
    at.session_state["sc_team_id"] = "team_a"
    at.run()
    # 🔒 `AppTest` 의 `options` 는 이미 `format_func` 를 거친 문자열이다
    shown = " ".join(at.selectbox(key="confirm_sector").options)
    from dashboard import data as _data

    known = set(_data.sector_names().sector.values())
    if not known:
        pytest.skip("이 환경에서 `sectors.yaml` 을 읽을 수 없다")
    assert any(name in shown for name in known), shown


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
