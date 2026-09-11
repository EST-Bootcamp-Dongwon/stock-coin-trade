"""시크릿 관문 테스트.

🔴 이 파일이 지키는 것은 확정 사실 **V19** 다 — `st.secrets` 는 시크릿 파일이
   없으면 **접근만으로** 예외를 던지고, 그 예외가 새면 Streamlit 화면이 통째로
   빈다. 관문이 절대 던지지 않는다는 것을 회귀로 박아 둔다.

🔒 테스트는 저장소 루트의 실제 `.env` 를 읽지 않는다 — `_repo_root` 를 tmp 로
   바꿔 끼운다. 그러지 않으면 개발자 기계 상태에 답이 달라진다.
"""

from __future__ import annotations

import sys

import pytest

from sector import secret_access as sa


@pytest.fixture(autouse=True)
def 빈_저장소_루트(tmp_path, monkeypatch):
    """`.env` 가 없는 루트를 기본값으로 둔다. 각 테스트가 필요하면 파일을 만든다."""
    monkeypatch.setattr(sa, "_repo_root", lambda: tmp_path)
    sa.reset_cache()
    yield tmp_path
    sa.reset_cache()


# ── 🔴 V19 ──────────────────────────────────────────────────────────────────

def test_시크릿파일이_없어도_관문은_던지지_않는다():
    """실측 근거: `st.secrets["K"]` · `"K" in st.secrets` · `st.secrets.get("K")` ·
    `bool(st.secrets)` 넷 다 `StreamlitSecretNotFoundError` 를 던진다
    (streamlit 1.63.0). 이 저장소에는 `.streamlit/secrets.toml` 이 없다(gitignore).
    """
    assert sa._from_streamlit("AMU_KEYDO_EOPNEUN_KI") is None


def test_streamlit_이_다른_예외로_바뀌어도_삼킨다(monkeypatch):
    """관문의 계약은 *"시크릿이 없을 때 절대 던지지 않는다"* 다.

    streamlit 이 마이너 버전에서 예외 계층을 바꿔도 빈 화면이 되살아나선 안 된다 —
    그래서 관문 한 곳에서만 `Exception` 을 넓게 잡는다.
    """

    class 낯선예외(Exception):
        pass

    class FakeSecrets:
        def __getitem__(self, key):
            raise 낯선예외("streamlit 이 계층을 바꿨다")

    fake = type(sys)("streamlit")
    fake.secrets = FakeSecrets()
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    assert sa._from_streamlit("K") is None


def test_streamlit_에_값이_있으면_그것을_쓴다(monkeypatch):
    class FakeSecrets:
        def __getitem__(self, key):
            return "from-cloud"

    fake = type(sys)("streamlit")
    fake.secrets = FakeSecrets()
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    assert sa.get_secret("KRX_API_KEY") == "from-cloud"


# ── 탐색 순서 ────────────────────────────────────────────────────────────────

def test_환경변수에서_읽는다(monkeypatch):
    monkeypatch.setenv("SOME_KEY", "env-value")
    assert sa.get_secret("SOME_KEY") == "env-value"


def test_환경변수가_dotenv_를_이긴다(빈_저장소_루트, monkeypatch):
    """셸에서 준 것이 파일을 이겨야 한다 — 일회성 덮어쓰기가 가능해야 디버깅이 된다."""
    (빈_저장소_루트 / ".env").write_text("SOME_KEY=file-value\n", encoding="utf-8")
    monkeypatch.setenv("SOME_KEY", "env-value")
    assert sa.get_secret("SOME_KEY") == "env-value"


def test_dotenv_에서_읽는다(빈_저장소_루트):
    (빈_저장소_루트 / ".env").write_text("SOME_KEY=file-value\n", encoding="utf-8")
    assert sa.get_secret("SOME_KEY") == "file-value"


# ── 빈 값은 없는 값이다 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("line", ["SOME_KEY=\n", "SOME_KEY=   \n", 'SOME_KEY=""\n'])
def test_빈_값은_없는_것으로_본다(빈_저장소_루트, line):
    """`.env` 에 이름만 적어 두고 채운 줄 알던 사고를 막는다."""
    (빈_저장소_루트 / ".env").write_text(line, encoding="utf-8")
    assert sa.get_secret("SOME_KEY", required=False) is None


def test_필수인데_없으면_어디에_넣으라고_말한다():
    with pytest.raises(sa.MissingSecretError) as excinfo:
        sa.get_secret("KRX_API_KEY")
    message = str(excinfo.value)
    # ★ 막다른 길로 만들지 않는다 — 무엇을 해야 하는지까지 적는다.
    assert "루트 .env" in message
    assert "backend/.env 가 아니다" in message     # `.env` 가 둘이라 실제로 헷갈린다
    assert "Streamlit Cloud" in message


def test_필수가_아니면_default_를_준다():
    assert sa.get_secret("NOPE", required=False, default="fallback") == "fallback"


# ── `.env` 파서 ──────────────────────────────────────────────────────────────

def test_dotenv_파서가_실제_포맷을_견딘다():
    text = "\n".join([
        "# 주석이다",
        "",
        "PLAIN=value",
        "  SPACED  =  padded  ",
        "export EXPORTED=exported-value",
        "QUOTED='single'",
        'DQUOTED="double"',
        "WITH_EQUALS=a=b=c",
        "EMPTY=",
        "# KRX 웹 로그인 — 주석에 = 가 있어도 무시한다",
        "NO_EQUALS_LINE",
    ])
    got = sa._parse_dotenv(text)
    assert got["PLAIN"] == "value"
    assert got["SPACED"] == "padded"
    assert got["EXPORTED"] == "exported-value"
    assert got["QUOTED"] == "single"
    assert got["DQUOTED"] == "double"
    assert got["WITH_EQUALS"] == "a=b=c"      # 첫 `=` 에서만 자른다
    assert got["EMPTY"] == ""
    assert "NO_EQUALS_LINE" not in got
    assert not any(k.startswith("#") for k in got)


def test_dotenv_가_없어도_조용히_넘어간다(빈_저장소_루트):
    assert not (빈_저장소_루트 / ".env").exists()
    assert sa._dotenv_values() == {}
