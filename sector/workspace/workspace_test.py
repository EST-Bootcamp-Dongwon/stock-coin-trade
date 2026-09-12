"""워크스페이스 계층의 계약 — 이벤트 · passcode · 저장소 · fold.

🔒 이 파일이 고정하는 것은 **성질**이다. "확정이 되나" 가 아니라 "두 번 눌러도
   원장이 더러워지지 않나 · 평문이 새지 않나 · 언제 돌려도 같은 답인가" 를 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sector.datastore import hub
from sector.workspace import auth, events, fold, store

AT1 = "2026-09-12T05:00:00+00:00"
AT2 = "2026-09-12T06:00:00+00:00"
AT3 = "2026-09-12T07:00:00+00:00"
#: 🔒 **가짜 해시다.** base64 를 풀면 `saltsaltsalt` · `hashhashhash` 라
#:    시크릿 스캐너가 걸어도 한눈에 픽스처임을 알 수 있게 해 뒀다.
HASH = "scrypt$16384$8$1$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNo"


def team(at: str = AT1, *, team_id: str = "team_a", actor: str = "동원") -> events.Event:
    return events.team_created(
        team_id=team_id, name="A조", passcode_hash=HASH, actor=actor, at=at)


# ── 이벤트 ──────────────────────────────────────────────────────────────────

def test_같은_내용은_같은_id_다():
    """🔒 멱등의 뿌리. 다르면 '확정' 두 번에 원장이 둘로 갈린다."""
    assert team().event_id == team().event_id


def test_내용이_한_글자_달라도_id_가_달라진다():
    a = events.sector_confirmed(team_id="team_a", sector_id="steel", reason="철강 1위",
                                actor="동원", at=AT1)
    b = events.sector_confirmed(team_id="team_a", sector_id="steel", reason="철강 2위",
                                actor="동원", at=AT1)
    assert a.event_id != b.event_id


def test_사전순이_시간순이다():
    """`bas_dd` 를 문자열로 둔 것과 같은 이유 — 정렬에 파싱이 필요 없다."""
    ids = [team(at).event_id for at in
           ("2026-09-12T05:00:00+00:00", "2026-09-12T05:00:01+00:00",
            "2026-09-13T00:00:00+00:00", "2026-10-01T00:00:00+00:00")]
    assert ids == sorted(ids)


def test_id_가_내용과_어긋나면_던진다():
    """손으로 고친 파일이 원장에 섞이면 원장이 원장이 아니게 된다."""
    data = team().to_json()
    data["payload"]["name"] = "B조"          # 내용만 바꾼다
    with pytest.raises(events.EventError, match="어긋난다"):
        events.parse_event(data)


def test_왕복한다():
    original = team()
    assert events.parse_event(json.loads(json.dumps(original.to_json()))) == original


@pytest.mark.parametrize("at", ["2026-09-12 05:00:00", "2026-09-12T05:00:00Z",
                                "2026-09-12T14:00:00+09:00", ""])
def test_UTC_ISO_가_아니면_던진다(at):
    """🔒 KST 로 사고하고 UTC 로 저장한다 — 두 모양이 섞이면 정렬이 거짓말을 한다."""
    with pytest.raises(events.EventError):
        team(at)


def test_확정에_사유가_없으면_받지_않는다():
    """🔴 사유 없는 확정은 이 도구가 존재하는 이유를 지운다."""
    for reason in ("", "   ", None):
        with pytest.raises(events.EventError, match="비어 있다"):
            events.sector_confirmed(team_id="team_a", sector_id="steel",
                                    reason=reason, actor="동원", at=AT1)


def test_평문_passcode_는_이벤트에_들어갈_수_없다():
    """🔒 규칙을 사람의 기억이 아니라 코드로 지킨다."""
    with pytest.raises(events.EventError, match="해시"):
        events.team_created(team_id="team_a", name="A조",
                            passcode_hash="산-바다-강-들", actor="동원", at=AT1)


def test_모르는_종류는_만들_수_없다():
    with pytest.raises(events.EventError, match="모르는 이벤트"):
        events.make_event("team.deleted", team_id="team_a", actor="x", at=AT1)


# ── passcode ────────────────────────────────────────────────────────────────

def test_맞으면_참_틀리면_거짓():
    stored = auth.hash_passcode("산-바다-강-들")
    assert auth.verify_passcode("산-바다-강-들", stored)
    assert not auth.verify_passcode("산-바다-강-숲", stored)


@pytest.mark.parametrize("stored", ["", "garbage", "scrypt$x$y$z$a$b",
                                    "scrypt$16384$8$1$###$###", "bcrypt$1$2$3$a$b"])
def test_깨진_해시는_던지지_않고_거짓이다(stored):
    """🔒 던지면 화면이 죽고, **죽는 방식이 답을 알려준다.**"""
    assert auth.verify_passcode("무엇이든", stored) is False


def test_평문이_해시에_남지_않는다():
    stored = auth.hash_passcode("산-바다-강-들")
    assert "산" not in stored and "바다" not in stored


def test_같은_passcode_라도_해시가_매번_다르다():
    """salt 가 무작위여야 한다 — 같으면 두 조가 같은 passcode 를 쓰는지 드러난다."""
    assert auth.hash_passcode("산-바다-강-들") != auth.hash_passcode("산-바다-강-들")


def test_salt_를_주면_결정적이다():
    """테스트가 언제 돌려도 같은 답이어야 한다 (AGENTS.md 5장)."""
    assert (auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
            == auth.hash_passcode("산-바다-강-들", salt=b"0" * 16))


def test_짧은_passcode_를_거부하고_평문을_말하지_않는다():
    with pytest.raises(auth.PasscodeError) as excinfo:
        auth.hash_passcode("1234")
    assert "1234" not in str(excinfo.value)


def test_저장된_파라미터로_검증한다():
    """🔒 나중에 비용(`SCRYPT_N`)을 올려도 **옛 해시가 계속 검증돼야 한다.**

    검증이 현재 상수를 쓰면, 비용을 올리는 순간 모든 조가 로그인하지 못한다.
    그래서 `verify_passcode` 는 저장된 문자열에 적힌 n·r·p 를 읽어 쓴다.
    """
    import base64
    import hashlib

    salt, secret = b"0" * 16, "산-바다-강-들"
    # 지금 상수보다 **싼** 파라미터로 만든 해시 — 옛날에 저장된 것을 흉내낸다
    old_n = 1024
    assert old_n != auth.SCRYPT_N
    digest = hashlib.scrypt(secret.encode(), salt=salt, n=old_n, r=8, p=1, dklen=32)
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")  # noqa: E731
    stored = f"scrypt${old_n}$8$1${b64(salt)}${b64(digest)}"

    assert auth.verify_passcode(secret, stored)
    assert not auth.verify_passcode("산-바다-강-숲", stored)


def test_제안된_passcode_가_항상_규칙을_통과한다():
    """🔴 한 번 뽑아 보는 것으로는 못 잡는다.

    한 글자 단어만 뽑히면 `산-강-들-숲` = 7자라 최소 길이에 걸린다. 실제로 그렇게
    터졌다 — 무작위라 **간헐적으로만** 드러나는 종류였다. 성질로 고정한다.
    """
    # 길이 성질은 많이 본다 — 이게 실제로 터진 지점이고 비용이 0 이다
    suggestions = [auth.suggest_passcode() for _ in range(500)]
    too_short = [s for s in suggestions if len(s) < auth.MIN_LENGTH]
    assert not too_short, f"최소 길이 미달 {len(too_short)}건: {too_short[:3]}"

    # 해시는 한 번에 약 30ms 라 몇 개만 — 계약은 위에서 이미 고정됐다
    for suggestion in suggestions[:3]:
        auth.hash_passcode(suggestion)      # 던지면 실패


def test_제안이_매번_다르다():
    assert len({auth.suggest_passcode() for _ in range(50)}) > 40


# ── 저장소 ──────────────────────────────────────────────────────────────────

def test_두_번_써도_하나다(tmp_path: Path):
    """🔒 '확정' 을 두 번 눌러도 원장이 더러워지지 않는다."""
    st = store.LocalStore(tmp_path)
    assert st.append([team()]) == 1
    assert st.append([team()]) == 0
    assert len(st.read_all()) == 1


def test_시간순으로_돌려준다(tmp_path: Path):
    st = store.LocalStore(tmp_path)
    st.append([team(AT3, team_id="team_c"), team(AT1, team_id="team_a"),
               team(AT2, team_id="team_b")])
    assert [e.at for e in st.read_all()] == [AT1, AT2, AT3]


def test_읽을_수_없는_줄을_삼키지_않는다(tmp_path: Path):
    st = store.LocalStore(tmp_path)
    st.append([team()])
    (tmp_path / "events" / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(store.StoreError):
        st.read_all()


def test_빈_원장은_빈_목록이다(tmp_path: Path):
    assert store.LocalStore(tmp_path).read_all() == []


def test_워크스페이스_경로가_점수_저장소로_못_간다():
    """🔒 경로가 맞아도 **저장소가 틀리면** 막는다."""
    event = team()
    hub.assert_publishable_path(event.path_in_repo, repo_id=hub.WORKSPACE_REPO_ID)
    with pytest.raises(hub.PublishBlocked):
        hub.assert_publishable_path(event.path_in_repo, repo_id=hub.REPO_ID)


# ── fold ────────────────────────────────────────────────────────────────────

def confirmed(sector_id: str, reason: str, at: str, actor: str = "동원") -> events.Event:
    return events.sector_confirmed(team_id="team_a", sector_id=sector_id, reason=reason,
                                   actor=actor, at=at)


def test_만든_사람이_조원이다():
    assert fold.fold([team()]).team("team_a").members == ("동원",)


def test_참가가_쌓이고_중복되지_않는다():
    joined = events.member_joined(team_id="team_a", member="민수", actor="민수", at=AT2)
    workspace = fold.fold([team(), joined, joined])
    assert workspace.team("team_a").members == ("동원", "민수")


def test_확정_전에는_None_이다():
    """🔒 빈 문자열도 '미정' 도 아니다 — 없으면 없다 (ADR-SC-0007)."""
    assert fold.fold([team()]).team("team_a").core_sector is None
    assert fold.fold([team()]).team("team_a").is_confirmed is False


def test_마지막_확정이_이기고_이력은_남는다():
    """대회 보고서의 '포폴 변경 사유' 가 이 이력에서 나온다."""
    workspace = fold.fold([team(), confirmed("steel", "철강 1위", AT2),
                           confirmed("chemical", "마음 바꿈", AT3, actor="민수")])
    current = workspace.team("team_a")
    assert (current.core_sector, current.core_reason, current.confirmed_by) == (
        "chemical", "마음 바꿈", "민수")
    assert len(workspace.history_of("team_a")) == 3


def test_취소하면_되돌아가되_기록은_남는다():
    cancel = events.sector_unconfirmed(team_id="team_a", reason="다시 논의", actor="동원", at=AT3)
    workspace = fold.fold([team(), confirmed("steel", "철강 1위", AT2), cancel])
    assert workspace.team("team_a").core_sector is None
    assert len(workspace.history_of("team_a")) == 3       # 지워지지 않는다


def test_없는_조의_이벤트를_드러낸다():
    """🔴 조용히 버리면 '왜 화면에 안 나오지' 로 돌아온다."""
    orphan = events.comment_posted(team_id="team_b", body="없는 조", actor="x", at=AT2)
    workspace = fold.fold([team(), orphan])
    assert workspace.comments == ()
    assert any("없는 조" in a for a in workspace.anomalies)


def test_같은_id_로_두_번_만들면_첫_번째가_이기고_알린다():
    workspace = fold.fold([team(AT1, actor="동원"), team(AT2, actor="민수")])
    assert workspace.team("team_a").created_by == "동원"
    assert any("두 번 만들어졌다" in a for a in workspace.anomalies)


def test_입력_순서가_달라도_같은_상태다():
    """🔒 원장에서 **순서는 유도되는 것**이다. 파일 목록 순서에 상태가 좌우되면 안 된다."""
    import random

    evs = [team(), events.member_joined(team_id="team_a", member="민수", actor="민수", at=AT2),
           confirmed("steel", "철강 1위", AT2), confirmed("chemical", "바꿈", AT3)]
    expected = fold.fold(evs).team("team_a")
    for seed in range(8):
        shuffled = evs[:]
        random.Random(seed).shuffle(shuffled)
        assert fold.fold(shuffled).team("team_a") == expected
        assert fold.fold(shuffled).anomalies == ()


def test_같은_초에_만들고_보관해도_인과가_지켜진다():
    """🔴 실제로 깨졌던 자리다.

    시각을 초 단위로 자르므로 한 초 안에 두 이벤트가 들어온다. 그때 tie-break 가
    `event_id` 사전순이면 `team-archived` 가 `team-created` 보다 앞서고
    (a < c), 접는 쪽은 "없는 조에 대한 보관" 을 보게 된다. 조를 만들자마자
    보관하면 재현된다.
    """
    same = AT1
    created = team(same)
    archived = events.team_archived(team_id="team_a", reason="바로 지운다",
                                    actor="동원", at=same)
    for order in ([created, archived], [archived, created]):
        workspace = fold.fold(order)
        assert workspace.anomalies == (), workspace.anomalies
        assert workspace.team("team_a").archived is True


def test_정렬_키를_store_와_fold_가_같이_쓴다():
    """🔒 둘이 갈라지면 '가끔 조가 사라진다' 로 나타난다."""
    import inspect

    from sector.workspace import store as store_module

    assert "sort_key" in inspect.getsource(store_module._sorted)
    assert "sort_key" in inspect.getsource(fold.fold)


def test_코멘트를_섹터로_거른다():
    both = [team(),
            events.comment_posted(team_id="team_a", body="철강 얘기", actor="동원",
                                  at=AT2, sector_id="steel"),
            events.comment_posted(team_id="team_a", body="일반 얘기", actor="민수", at=AT3)]
    workspace = fold.fold(both)
    assert len(workspace.comments_of("team_a")) == 2
    assert len(workspace.comments_of("team_a", sector_id="steel")) == 1


# ── 벽시계·평문 정적 검사 ───────────────────────────────────────────────────

def test_워크스페이스_계층이_벽시계를_읽지_않는다():
    """🔴 시각은 **입력**이다. 도메인이 시계를 읽으면 같은 원장이 다른 상태를 낸다."""
    for module in (events, fold, store):
        body = Path(module.__file__).read_text(encoding="utf-8").split('"""', 2)[-1]
        for banned in ("datetime.now", "time.time", "utcnow", "date.today"):
            assert banned not in body, f"`{banned}` 가 {module.__name__} 에 있다"


def test_이벤트_JSON_에_평문_passcode_가_없다(tmp_path: Path):
    """🔒 원장에 쓰이는 실제 바이트를 본다 — 계약이 아니라 결과를 확인한다."""
    secret = "산-바다-강-들"
    st = store.LocalStore(tmp_path)
    st.append([events.team_created(team_id="team_a", name="A조",
                                   passcode_hash=auth.hash_passcode(secret),
                                   actor="동원", at=AT1)])
    for path in (tmp_path / "events").glob("*.json"):
        assert secret not in path.read_text(encoding="utf-8")


# ── 두 구현이 같은 계약을 지킨다 ────────────────────────────────────────────

class FakeHubApi:
    """`HubStore` 가 기대하는 만큼만 흉내낸다. 🔒 네트워크를 부르지 않는다."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commits = 0

    def repo_info(self, **_: object):
        return type("Info", (), {"private": True})()

    def list_repo_files(self, **_: object) -> list[str]:
        return sorted(self.files)

    def create_commit(self, *, operations, **_: object):
        for op in operations:
            self.files[op.path_in_repo] = bytes(op.path_or_fileobj)
        self.commits += 1
        return type("Commit", (), {"oid": "fake"})()

    def hf_hub_download(self, *, filename: str, **_: object) -> str:
        import tempfile
        path = Path(tempfile.mkdtemp()) / "f"
        path.write_bytes(self.files[filename])
        return str(path)


def test_두_구현이_멱등에_같은_답을_낸다(tmp_path: Path):
    """🔴 실서버에서 어긋났던 지점이다.

    `HubStore` 가 이미 있는 이벤트를 그대로 커밋하면 `huggingface_hub` 가
    *"No files have been modified"* 로 건너뛰는데, 우리는 "N건 올렸다" 고 답했다.
    **0건 쓰고 N건이라 답하는 것**은 화면이 "기록됐다" 고 거짓말하게 만든다.
    """
    event = team()
    local, remote = store.LocalStore(tmp_path), store.HubStore(FakeHubApi())

    assert local.append([event]) == remote.append([event]) == 1
    assert local.append([event]) == remote.append([event]) == 0      # ← 여기가 어긋났었다
    assert len(local.read_all()) == len(remote.read_all()) == 1


def test_올릴_것이_없으면_커밋하지_않는다():
    """빈 커밋은 원장에 잡음만 남긴다."""
    api = FakeHubApi()
    remote = store.HubStore(api)
    remote.append([team()])
    assert api.commits == 1
    remote.append([team()])
    assert api.commits == 1          # 늘지 않는다


def test_HubStore_도_시간순으로_돌려준다():
    remote = store.HubStore(FakeHubApi())
    remote.append([team(AT3, team_id="team_c"), team(AT1, team_id="team_a"),
                   team(AT2, team_id="team_b")])
    assert [e.at for e in remote.read_all()] == [AT1, AT2, AT3]


def test_목록을_못_받으면_던진다():
    class Broken(FakeHubApi):
        def list_repo_files(self, **_: object):
            raise ConnectionError("끊김")

    with pytest.raises(store.StoreError, match="파일 목록"):
        store.HubStore(Broken()).read_all()


# ── 마스터 계정 ─────────────────────────────────────────────────────────────

def test_시크릿이_없으면_아무도_마스터가_아니다(monkeypatch):
    """🔴 '설정 안 했으니 모두 통과' 가 되면 정반대가 된다."""
    monkeypatch.setattr(auth, "master_hash", lambda: None)
    assert auth.is_master("무엇이든") is False
    assert auth.is_master("") is False


def test_마스터_해시가_있으면_맞는_것만_통과한다(monkeypatch):
    stored = auth.hash_passcode("마스터-산-바다-강", salt=b"0" * 16)
    monkeypatch.setattr(auth, "master_hash", lambda: stored)
    assert auth.is_master("마스터-산-바다-강")
    assert not auth.is_master("마스터-산-바다-숲")


def test_평문이_들어오면_해시로_인정하지_않는다(monkeypatch):
    """🔒 `.env` 에 평문을 넣어도 마스터가 되지 않는다 — 형식을 본다."""
    from sector import secret_access

    monkeypatch.setattr(secret_access, "get_secret",
                        lambda name, **kw: "마스터-산-바다-강")
    assert auth.master_hash() is None


# ── 보관 ────────────────────────────────────────────────────────────────────

def test_보관하면_목록에서_빠지되_기록은_남는다():
    archived = events.team_archived(team_id="team_a", reason="테스트 조였다",
                                    actor="동원", at=AT2)
    workspace = fold.fold([team(), archived])
    assert workspace.team("team_a").archived is True
    assert "team_a" not in workspace.active_teams
    assert "team_a" in workspace.archived_teams
    assert len(workspace.history_of("team_a")) == 2      # 지워지지 않는다


def test_보관을_되돌릴_수_있다():
    evs = [team(),
           events.team_archived(team_id="team_a", reason="잘못 눌렀다", actor="동원", at=AT2),
           events.team_restored(team_id="team_a", reason="다시 쓴다", actor="동원", at=AT3)]
    workspace = fold.fold(evs)
    assert workspace.team("team_a").archived is False
    assert "team_a" in workspace.active_teams


def test_보관에도_사유가_필요하다():
    """🔒 '왜 지웠나' 가 남지 않으면 3개월 뒤에 설명할 수 없다."""
    with pytest.raises(events.EventError, match="비어 있다"):
        events.team_archived(team_id="team_a", reason="", actor="동원", at=AT2)


def test_확정했던_섹터는_보관해도_기록에_남는다():
    """🔴 이것이 '지우지 않고 보관' 의 요점이다."""
    evs = [team(), confirmed("steel", "철강 1위", AT2),
           events.team_archived(team_id="team_a", reason="정리", actor="동원", at=AT3)]
    workspace = fold.fold(evs)
    assert workspace.team("team_a").core_sector == "steel"     # 확정은 그대로다
    assert workspace.team("team_a").archived is True
