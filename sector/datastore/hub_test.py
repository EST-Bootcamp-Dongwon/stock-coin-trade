"""`sector/datastore/hub.py` 테스트 — **문**이 실제로 잠기는가.

이 파일이 증명해야 하는 문장은 하나다:
🔴 **"저장소가 public 이면 한 파일도 올라가지 않는다."**
그 외의 검사는 그 문장을 떠받친다.

실제 HF 를 부르지 않는다. 가짜 API 를 주고 **무엇이 불렸는가**를 본다 —
"올리지 않았다"는 `create_commit` 이 **불리지 않았다**로만 증명된다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import requests
from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError

from sector.datastore import hub


def _response(status: int = 404):
    response = requests.Response()
    response.status_code = status
    return response


class FakeApi:
    """HF 대신 답하는 가짜. 불린 호출을 전부 기록한다."""

    def __init__(self, *, private=True, exists=True, files: dict[str, bytes] | None = None):
        self.private = private
        self.exists = exists
        self.files = files or {}
        self.commits: list[dict] = []
        self.created: list[dict] = []
        self.info_calls = 0

    def repo_info(self, *, repo_id, repo_type):
        self.info_calls += 1
        if not self.exists:
            raise RepositoryNotFoundError("없다", response=_response())
        return SimpleNamespace(private=self.private, id=repo_id)

    def create_repo(self, *, repo_id, repo_type, private, exist_ok):
        self.created.append({"repo_id": repo_id, "private": private})
        self.exists = True
        self.private = private

    def create_commit(self, *, repo_id, repo_type, operations, commit_message,
                      commit_description=""):
        self.commits.append({"operations": list(operations), "message": commit_message})
        return SimpleNamespace(oid="deadbeef" * 5)

    def hf_hub_download(self, *, repo_id, repo_type, filename):
        if not self.exists:
            raise RepositoryNotFoundError("없다", response=_response())
        if filename not in self.files:
            raise EntryNotFoundError("파일이 없다")
        path = _TMP / filename.replace("/", "_")
        path.write_bytes(self.files[filename])
        return str(path)


_TMP = None


@pytest.fixture(autouse=True)
def _tmp(tmp_path):
    global _TMP
    _TMP = tmp_path
    yield


class Add:
    """`CommitOperationAdd` 대역 — 경로만 있으면 게이트를 시험할 수 있다."""

    def __init__(self, path_in_repo: str):
        self.path_in_repo = path_in_repo


# ── 경로 허용목록 ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "MANIFEST.json", "README.md",
    "latest/snapshot.json", "latest/sector_daily_latest.parquet",
    "sector_daily/year=2026/month=09/sector_daily_202609.parquet",
    "market_daily/year=2025/month=07/market_daily_202507.parquet",
    "score_daily/year=2026/month=09/score_daily_202609.parquet",
])
def test_허용된_경로는_지나간다(path):
    hub.assert_publishable_path(path)


@pytest.mark.parametrize("path", [
    "data/raw/etf_bydd_trd_20260910.json.gz",
    "etf_bydd_trd_20260910.parquet",
    "latest/stk_bydd_trd_20260910.json",
    "latest/ksq_bydd_trd.parquet",
    "latest/OutBlock_1.json",
])
def test_원천은_이름만으로_막힌다(path):
    """🔴 ADR-SC-0006 ① — KRX 원천은 `data/raw/` 를 떠나지 않는다."""
    with pytest.raises(hub.PublishBlocked, match="원천"):
        hub.assert_publishable_path(path)


@pytest.mark.parametrize("path", ["/etc/passwd", "../secrets", "a/../../b", "c:\\x", "", " x"])
def test_저장소_밖이나_이상한_경로를_막는다(path):
    with pytest.raises(hub.PublishBlocked):
        hub.assert_publishable_path(path)


def test_허용목록에_없는_경로를_막고_무엇이_허용인지_말한다():
    with pytest.raises(hub.PublishBlocked) as caught:
        hub.assert_publishable_path("notes/team.json")
    assert "허용" in str(caught.value)


# ── private 게이트 ──────────────────────────────────────────────────────────

def test_private_이면_지나간다():
    hub.assert_private(FakeApi(private=True), "x/y")


def test_public_이면_던진다():
    with pytest.raises(hub.PublishBlocked, match="private"):
        hub.assert_private(FakeApi(private=False), "x/y")


def test_저장소가_없으면_무엇을_확인할지_말한다():
    with pytest.raises(hub.PublishBlocked, match="권한"):
        hub.assert_private(FakeApi(exists=False), "x/y")


def test_public_이면_한_파일도_올리지_않는다():
    """🔴 M6 의 완료 조건. '올리지 않았다'를 `create_commit` 미호출로 증명한다."""
    api = FakeApi(private=False)
    with pytest.raises(hub.PublishBlocked):
        hub.commit(api, [Add("latest/snapshot.json")], message="m", repo_id="x/y")
    assert api.commits == []


def test_경로가_하나라도_어긋나면_한_파일도_올리지_않는다():
    """부분 업로드가 없다 — 검사가 커밋 **전에** 전부 끝난다."""
    api = FakeApi(private=True)
    ops = [Add("latest/snapshot.json"), Add("data/raw/etf_bydd_trd_20260910.json.gz")]
    with pytest.raises(hub.PublishBlocked):
        hub.commit(api, ops, message="m", repo_id="x/y")
    assert api.commits == []


def test_private_확인이_커밋보다_먼저다():
    """순서가 뒤집히면 '올린 뒤에 막았다'가 된다. 그건 막은 것이 아니다."""
    api = FakeApi(private=False)
    with pytest.raises(hub.PublishBlocked):
        hub.commit(api, [Add("README.md")], message="m", repo_id="x/y")
    assert api.info_calls >= 1 and api.commits == []


def test_단일_커밋으로_올린다():
    """🔴 파일마다 올리면 중간 실패가 '절반만 갱신된' 상태를 남긴다."""
    api = FakeApi(private=True)
    ops = [Add("README.md"), Add("MANIFEST.json"), Add("latest/snapshot.json")]
    oid = hub.commit(api, ops, message="m", repo_id="x/y")
    assert len(api.commits) == 1
    assert len(api.commits[0]["operations"]) == 3
    assert oid.startswith("deadbeef")


def test_올릴_것이_없으면_커밋을_부르지_않는다():
    api = FakeApi(private=True)
    with pytest.raises(ValueError):
        hub.commit(api, [], message="m", repo_id="x/y")
    assert api.commits == []


# ── 저장소 만들기 ───────────────────────────────────────────────────────────

def test_없으면_private_으로_만든다():
    api = FakeApi(exists=False)
    assert hub.ensure_private_repo(api, "x/y") is True
    assert api.created == [{"repo_id": "x/y", "private": True}]


def test_이미_있으면_건드리지_않는다():
    api = FakeApi(exists=True, private=False)
    assert hub.ensure_private_repo(api, "x/y") is False
    assert api.created == []      # 🔒 public 을 private 으로 조용히 바꾸지 않는다


def test_만들었다는_사실이_private_을_보장하지_않는다():
    """`create_repo(exist_ok=True)` 는 public 대상에도 성공한다 — 그래서 다시 묻는다."""
    api = FakeApi(exists=True, private=False)
    hub.ensure_private_repo(api, "x/y")
    with pytest.raises(hub.PublishBlocked):
        hub.assert_private(api, "x/y")


# ── 읽기 ────────────────────────────────────────────────────────────────────

def test_없는_파일은_None_이다():
    assert hub.read_json(FakeApi(), "MANIFEST.json", repo_id="x/y") is None


def test_없는_저장소도_None_이다():
    """첫 게시에 MANIFEST 가 없는 것은 오류가 아니다."""
    assert hub.read_json(FakeApi(exists=False), "MANIFEST.json", repo_id="x/y") is None


def test_있는_JSON_을_읽는다():
    api = FakeApi(files={"MANIFEST.json": json.dumps({"a": 1}).encode()})
    assert hub.read_json(api, "MANIFEST.json", repo_id="x/y") == {"a": 1}


def test_깨진_JSON_은_없음으로_취급하지_않는다():
    """🔴 '없음'으로 삼키면 매번 전부 다시 올리면서 아무도 그 사실을 모른다."""
    api = FakeApi(files={"MANIFEST.json": b"{not json"})
    with pytest.raises(hub.HubError, match="JSON"):
        hub.read_json(api, "MANIFEST.json", repo_id="x/y")


# ── 토큰 ────────────────────────────────────────────────────────────────────

def test_토큰이_없으면_무엇을_해야_하는지까지_말한다(monkeypatch):
    monkeypatch.setattr(hub, "get_secret", lambda name, required=True: None)
    with pytest.raises(hub.HubError) as caught:
        hub.write_token()
    message = str(caught.value)
    assert hub.TOKEN_ENV_WRITE in message and "huggingface.co/settings/tokens" in message


def test_읽기_토큰과_쓰기_토큰의_이름이_다르다():
    """🔒 같은 이름을 쓰면 앱 시크릿에 쓰기 권한이 넘어간다 (2026-09-11 실제 사례)."""
    assert hub.TOKEN_ENV_READ != hub.TOKEN_ENV_WRITE


def test_토큰을_읽는_길이_secret_access_하나다(monkeypatch):
    """확정 사실 V19 — `st.secrets` 는 접근만으로 던진다. 관문이 둘이면 하나는 잊는다."""
    seen: list[str] = []

    def fake(name, required=True):
        seen.append(name)
        return "hf_fake"

    monkeypatch.setattr(hub, "get_secret", fake)
    assert hub.write_token() == "hf_fake"
    assert hub.read_token() == "hf_fake"
    assert seen == [hub.TOKEN_ENV_WRITE, hub.TOKEN_ENV_READ]
