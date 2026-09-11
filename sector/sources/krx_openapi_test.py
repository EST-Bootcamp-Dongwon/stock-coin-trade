"""KRX 어댑터 단위 테스트.

🔒 **실제 KRX 데이터를 픽스처로 쓰지 않는다** (→ `AGENTS.md` 5장 · 제약 10).
   `sector/testdata/etf_bydd_trd_synthetic.json` 은 합성 데이터이고, 2026-09-11 에
   실제 원천 1168행을 관찰해 **발견한 엣지 케이스만** 같은 모양으로 재현해 둔 것이다.
   값 자체는 실재하지 않는다.

🔒 네트워크를 부르지 않는다. HTTP 는 가짜 세션으로 주입한다 — 테스트는 "언제 돌려도
   같은 답"을 내야 한다 (`AGENTS.md` 5장).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from sector.sources import krx_openapi as krx

FIXTURE = Path(__file__).resolve().parents[1] / "testdata" / "etf_bydd_trd_synthetic.json"
BAS_DD = "20260910"


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def quotes(payload) -> list[krx.EtfDailyQuote]:
    return krx.parse_payload(payload, expected_bas_dd=BAS_DD)


def by_code(quotes, code):
    (found,) = [q for q in quotes if q.isu_cd == code]
    return found


# ── 🔴 이 파일의 핵심 불변식 ─────────────────────────────────────────────────

def test_빈문자열은_None_이고_0은_0이다(quotes):
    """이 어댑터가 존재하는 이유다 (→ ADR-SC-0007).

    빈 문자열을 0 으로 읽으면 거래대금 0 원인 ETF 가 생겨 유동성 게이트가
    오작동하고, 0 을 결측으로 읽으면 거래가 없던 날이 누락으로 보고된다.
    """
    없는칸 = by_code(quotes, "900003")     # 기초지수 미공표 ETF
    assert 없는칸.idx_close is None
    assert 없는칸.idx_chg is None
    assert 없는칸.idx_fluc_rt_bp is None

    진짜0 = by_code(quotes, "900004")      # 거래가 없던 날
    assert 진짜0.acc_trdvol == 0
    assert 진짜0.acc_trdval == 0
    assert 진짜0.acc_trdvol is not None    # 0 이 falsy 라는 이유로 결측 취급되면 안 된다


def test_기초지수가_없어도_행을_버리지_않는다(quotes):
    """부분 결측은 **필드 단위**다. 행 단위로 버리면 그 섹터의 자금흐름이 사라진다."""
    q = by_code(quotes, "900003")
    assert q.nav == Decimal("5195.88")
    assert q.list_shrs == 5_000_000       # 자금흐름 축의 유일한 입력이 살아 있다
    assert q.close_prc == 5200
    assert len(quotes) == 6               # 6행 모두 살아남는다


def test_종목코드에_영문자가_있어도_통과한다(quotes):
    """실제 원천 1168행 중 **304행**이 `9999A9` 꼴이다.

    `\\d{6}` 으로 검증하던 코드는 ETF 304개를 조용히 잃는다.
    """
    q = by_code(quotes, "9000A2")
    assert q.isu_nm == "합성 2차전지 ETF"


def test_결측토큰_대시도_None_이다(quotes):
    q = by_code(quotes, "900006")
    assert q.chg_prc is None
    assert q.fluc_rt_bp is None
    assert q.close_prc == 10_050          # 같은 행의 다른 칸은 멀쩡하다


# ── 타입 규약 — `float` 금지 ─────────────────────────────────────────────────

def test_비율은_bp_정수다(quotes):
    assert by_code(quotes, "900001").fluc_rt_bp == 152        # "1.52" %
    assert by_code(quotes, "9000A2").fluc_rt_bp == 0          # "0.00" %
    assert by_code(quotes, "900005").fluc_rt_bp == -283       # "-2.83" %
    assert by_code(quotes, "900005").idx_fluc_rt_bp == -282   # 부호가 살아남는다


def test_어디에도_float_이_없다(quotes):
    """🔒 `AGENTS.md` 4장 — 누적 반올림이 새는 것을 구조적으로 막는다."""
    for q in quotes:
        for name in q.__slots__:
            value = getattr(q, name)
            assert not isinstance(value, float), f"{q.isu_cd}.{name} 이 float 다"


def test_NAV_는_문자열에서_바로_Decimal_이_된다(quotes):
    nav = by_code(quotes, "900001").nav
    assert nav == Decimal("10012.34")
    assert isinstance(nav, Decimal)
    # float 을 경유했다면 정확히 같지 않다.
    assert str(nav) == "10012.34"


# ── 이상값은 던진다. 삼키지 않는다 ──────────────────────────────────────────

def test_숫자로_읽을_수_없으면_None_이_아니라_예외다(payload):
    payload["OutBlock_1"][0]["LIST_SHRS"] = "1.2조"
    with pytest.raises(krx.KrxSchemaError, match="LIST_SHRS"):
        krx.parse_payload(payload, expected_bas_dd=BAS_DD)


def test_소수자릿수가_늘면_반올림하지_않고_던진다(payload):
    """원천이 소수 3자리로 바뀌면 조용히 값을 바꾸는 대신 멈춘다."""
    payload["OutBlock_1"][0]["FLUC_RT"] = "1.234"
    with pytest.raises(krx.KrxSchemaError, match="bp 정수"):
        krx.parse_payload(payload, expected_bas_dd=BAS_DD)


def test_영업일이_요청과_다르면_던진다(payload):
    """stale·룩어헤드가 조용히 섞이면 뒤에서 찾을 수 없다."""
    payload["OutBlock_1"][2]["BAS_DD"] = "20260909"
    with pytest.raises(krx.KrxSchemaError, match="다르다"):
        krx.parse_payload(payload, expected_bas_dd=BAS_DD)


def test_종목코드가_망가지면_던진다(payload):
    payload["OutBlock_1"][0]["ISU_CD"] = "90001"      # 5자리
    with pytest.raises(krx.KrxSchemaError, match="ISU_CD"):
        krx.parse_payload(payload, expected_bas_dd=BAS_DD)


def test_결과블록이_없으면_respMsg_를_실어_던진다():
    with pytest.raises(krx.KrxSchemaError, match="Unauthorized Key"):
        krx.parse_payload({"respMsg": "Unauthorized Key", "respCode": "401"})


# ── 휴일은 오류가 아니다 ─────────────────────────────────────────────────────

def test_휴일은_빈_리스트다():
    """원천이 실제로 이렇게 답한다 (일요일 요청 → HTTP 200 + 빈 블록)."""
    assert krx.parse_payload({"OutBlock_1": []}, expected_bas_dd=BAS_DD) == []


# ── 영업일 검증 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["2026-09-10", "202609", "abcdefgh", "20260231", ""])
def test_잘못된_영업일은_받지_않는다(bad):
    """`20260231` 은 형식은 맞지만 달력에 없다 — 통과시키면 원천이 0건으로 답해
    '휴일이라 0건'과 구별할 수 없게 된다."""
    with pytest.raises(ValueError):
        krx._validate_bas_dd(bad)


# ── HTTP 경로 — 가짜 세션 ────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None, text_body: str | None = None):
        self.status_code = status_code
        self._body = body
        self._text = text_body

    def json(self):
        if self._text is not None:
            raise ValueError("not json")
        return self._body


class FakeSession:
    """`requests` 를 대신한다. 호출 횟수를 세는 것이 요점이다."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, *, params, headers, timeout):
        self.calls.append({"url": url, "params": params, "headers": headers})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_정상_취득(payload):
    session = FakeSession([FakeResponse(200, payload)])
    got = krx.fetch_etf_daily(BAS_DD, auth_key="key", session=session)
    assert len(got) == 6
    call = session.calls[0]
    assert call["params"] == {"basDd": BAS_DD}
    # ★ `Authorization` 이 아니라 KRX 고유 헤더다. 틀리면 401 이 돌아온다.
    assert call["headers"] == {"AUTH_KEY": "key"}


def test_401은_재시도하지_않는다():
    """재시도해도 같은 답이고, 키당 일 10,000회 예산만 태운다."""
    session = FakeSession([FakeResponse(401, {"respMsg": "Unauthorized Key"})])
    with pytest.raises(krx.KrxAuthError, match="개별 API 이용신청"):
        krx.fetch_etf_daily(BAS_DD, auth_key="bad", session=session, retries=3)
    assert len(session.calls) == 1


def test_5xx_는_재시도하고_끝내_전송오류다():
    session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(503)])
    slept: list[float] = []
    with pytest.raises(krx.KrxTransportError):
        krx.fetch_etf_daily(
            BAS_DD, auth_key="key", session=session, retries=2, sleep=slept.append
        )
    assert len(session.calls) == 3        # 첫 시도 + 재시도 2회
    assert len(slept) == 2                # 마지막 실패 뒤에는 기다리지 않는다


def test_전송오류_뒤_성공하면_값을_준다(payload):
    session = FakeSession([OSError("연결 끊김"), FakeResponse(200, payload)])
    got = krx.fetch_etf_daily(
        BAS_DD, auth_key="key", session=session, retries=2, sleep=lambda _: None
    )
    assert len(got) == 6
    assert len(session.calls) == 2


def test_JSON_이_아니면_스키마오류다():
    session = FakeSession([FakeResponse(200, text_body="<html>")])
    with pytest.raises(krx.KrxSchemaError, match="JSON"):
        krx.fetch_etf_daily(BAS_DD, auth_key="key", session=session)


# ── 🔴 원천 유출 방지 — 규칙을 코드로 막았는지 본다 ──────────────────────────

def test_원천_저장경로는_data_raw_안이고_gitignore_된다():
    path = krx.raw_output_path(BAS_DD)
    assert path.parent.name == "raw"
    assert path.parent.parent.name == "data"
    krx._assert_raw_output_allowed(path)   # 던지지 않아야 한다


def test_data_raw_밖은_거부한다(tmp_path):
    """약관 제11조② — Public 저장소에 KRX 원천이 커밋되는 경로를 코드로 막는다."""
    with pytest.raises(RuntimeError, match="data/raw"):
        krx._assert_raw_output_allowed(tmp_path / "etf.json")
    with pytest.raises(RuntimeError, match="data/raw"):
        krx._assert_raw_output_allowed(krx._repo_root() / "etf_bydd_trd.json")


def test_요약은_시세값을_찍지_않는다(quotes, payload):
    """`--probe` 출력은 터미널·로그·세션 기록에 남는다. 값이 새면 `data/raw/` 로
    막아 둔 것이 표준출력으로 빠져나간다."""
    text = krx.summarize(quotes, payload["OutBlock_1"])
    for leak in ["10012.34", "500000000000", "합성 반도체 ETF", "2500.00"]:
        assert leak not in text
    assert "행 수: 6" in text
    assert "거래량이 실제로 0 인 행: 1건" in text


def test_요약이_새_필드를_감지한다(quotes, payload):
    rows = [dict(r) for r in payload["OutBlock_1"]]
    rows[0]["NEW_FIELD"] = "x"
    text = krx.summarize(quotes, rows)
    assert "새 필드" in text and "NEW_FIELD" in text
