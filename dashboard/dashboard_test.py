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
