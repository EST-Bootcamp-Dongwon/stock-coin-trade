"""워크스페이스 계층의 계약 — 이벤트 · passcode · 저장소 · fold.

🔒 이 파일이 고정하는 것은 **성질**이다. "확정이 되나" 가 아니라 "두 번 눌러도
   원장이 더러워지지 않나 · 평문이 새지 않나 · 언제 돌려도 같은 답인가" 를 본다.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from sector.datastore import hub
from sector.workspace import auth, events, fold, links, store

AT1 = "2026-09-12T05:00:00+00:00"
AT2 = "2026-09-12T06:00:00+00:00"
AT3 = "2026-09-12T07:00:00+00:00"
#: 🔒 **가짜 해시다.** base64 를 풀면 `saltsaltsalt` · `hashhashhash` 라
#:    시크릿 스캐너가 걸어도 한눈에 픽스처임을 알 수 있게 해 뒀다.
HASH = "scrypt$16384$8$1$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNo"


def team(at: str = AT1, *, team_id: str = "team_a", actor: str = "동원") -> events.Event:
    return events.team_created(team_id=team_id, name="A조", actor=actor, at=at)


def make(store_, at: str = AT1, *, team_id: str = "team_a", actor: str = "동원",
         passcode_hash: str = HASH) -> events.Event:
    """조를 만든다 — 🔒 `create_team` 을 지난다. `append` 로는 만들 수 없다."""
    event = team(at, team_id=team_id, actor=actor)
    store_.create_team(event, passcode_hash=passcode_hash)
    return event


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


@pytest.mark.parametrize("key", ["passcode_hash", "passcode", "PASSCODE", "team_passcode"])
def test_passcode_는_어떤_payload_에도_들어갈_수_없다(key):
    """🔴 DB 의 `check (not (payload ? 'passcode_hash'))` 와 **같은 것**을 막는다.

    이쪽이 더 넓다(`passcode` 를 품은 모든 키). 넓은 쪽이 먼저 거부하므로 "앱이
    만든 이벤트를 DB 가 거부한다" 는 일이 생기지 않는다 — 좁으면 생긴다.
    """
    with pytest.raises(events.EventError, match="passcode"):
        events.make_event("comment.posted", team_id="team_a", actor="동원", at=AT1,
                          payload={"body": "x", key: "scrypt$1$2$3$a$b"})


def test_조_생성_이벤트에_해시가_없다():
    """🔒 원장 읽기가 공개다 — 해시가 원장에 있으면 그것이 곧 노출이다."""
    assert "passcode_hash" not in team().payload
    assert set(team().payload) == {"name"}


def test_옛_원장은_읽히고_어긋난_것으로_올라온다():
    """🔴 읽기를 막으면 옛 원장 한 줄이 팀 전체 화면을 죽인다.

    그래서 `parse_event` 는 게이트를 지나지 않고(`_make`), 대신 `fold` 가 말한다 —
    "이 조에는 참가할 수 없다".
    """
    legacy = events.make_event("comment.posted", team_id="team_a", actor="동원", at=AT1,
                               payload={"body": "옛 원장 흉내"}).to_json()
    legacy["payload"]["passcode_hash"] = HASH
    # id 는 내용에서 나오므로 옛 파일과 같은 방식으로 다시 계산해 넣는다
    rebuilt = events._make(                                    # noqa: SLF001 — 옛 원장 재현
        legacy["kind"], team_id=legacy["team_id"], actor=legacy["actor"],
        at=legacy["at"], payload=legacy["payload"])
    legacy["event_id"] = rebuilt.event_id

    parsed = events.parse_event(legacy)                        # 🔒 던지지 않는다
    assert events.carries_legacy_secret(parsed)
    workspace = fold.fold([team(), parsed])
    assert any("옛 형식" in a for a in workspace.anomalies), workspace.anomalies


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


def test_파라미터에서_digest_를_뗀다():
    """🔒 SQL `workspace_passcode_params` 와 **같은 것**을 한다."""
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    params = auth.params_of(stored)
    assert stored.startswith(params) and params.endswith("$")
    assert params.count("$") == 5                  # scrypt$n$r$p$salt$
    assert stored.split("$")[5] not in params      # 🔴 digest 가 없다
    assert auth.params_of(params + "x") == params  # 무엇이 붙어 있어도 떼어 낸다


def test_저장된_salt_로_재계산하면_바이트가_같다():
    """🔴 DB 는 문자열을 **상수시간으로 통째 비교**한다 — 한 글자만 달라도 거부다."""
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    again = auth.recompute_passcode("산-바다-강-들", auth.params_of(stored))
    assert again == stored
    assert auth.recompute_passcode("산-바다-강-숲", auth.params_of(stored)) != stored


def test_재계산에_digest_를_넘기면_거부한다():
    """🔴 호출부가 저장된 해시를 통째로 들고 있다는 뜻이다 — ④ 가 깨진 상태다."""
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    with pytest.raises(auth.PasscodeError, match="digest"):
        auth.recompute_passcode("산-바다-강-들", stored)


@pytest.mark.parametrize("params", ["", "garbage", "scrypt$16384$8$1$",
                                    "bcrypt$1$2$3$YWFhYWFhYWFhYWFh$",
                                    "scrypt$x$y$z$YWFhYWFhYWFhYWFh$",
                                    "scrypt$16384$8$1$$"])
def test_깨진_파라미터는_던진다(params):
    """🔒 여기서는 **던진다.** 검증(`verify_passcode`)과 달리 이것은 설정 문제다 —
    조용히 `False` 로 만들면 "passcode 가 틀렸다" 로 보이고 원인을 못 찾는다."""
    with pytest.raises(auth.PasscodeError):
        auth.recompute_passcode("산-바다-강-들", params)


def test_자격증명_비교가_상수시간이고_틀린_이유를_말하지_않는다():
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    assert auth.credential_matches(stored, stored) is True
    assert auth.credential_matches(stored[:-1], stored) is False
    for bad in ("", None, 0, stored + "x"):
        assert auth.credential_matches(bad, stored) is False   # type: ignore[arg-type]
    assert auth.credential_matches(stored, "") is False


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
    make(st)
    event = confirmed("steel", "철강 1위", AT2)
    assert st.append([event]) == 1
    assert st.append([event]) == 0
    assert len(st.read_all().events) == 2


def test_시간순으로_돌려준다(tmp_path: Path):
    st = store.LocalStore(tmp_path)
    make(st, AT3, team_id="team_c")
    make(st, AT1, team_id="team_a")
    make(st, AT2, team_id="team_b")
    assert [e.at for e in st.read_all().events] == [AT1, AT2, AT3]


def test_읽을_수_없는_줄이_원장_전체를_멈추지_않는다(tmp_path: Path):
    """🔴 한 줄이 **모든 조**의 원장 화면을 멈췄고, append-only 라 지울 수도 없었다.

    건너뛰되 삼키지 않는다 — `Ledger.rejected` 로 올라가 `fold` 가 말한다.
    """
    st = store.LocalStore(tmp_path)
    make(st, team_id="team_a")
    make(st, AT2, team_id="team_b")
    (tmp_path / "events" / "broken.json").write_text("{", encoding="utf-8")
    bad = events.comment_posted(team_id="team_b", body="원래 본문", actor="동원", at=AT3).to_json()
    bad["payload"]["body"] = "id 와 어긋난 본문"          # id 는 그대로 — RPC 가 받아 주는 모양
    (tmp_path / "events" / f"{bad['event_id']}.json").write_text(
        json.dumps(bad, ensure_ascii=False), encoding="utf-8")

    ledger = st.read_all()
    assert [e.team_id for e in ledger.events] == ["team_a", "team_b"]
    assert len(ledger.rejected) == 2
    workspace = fold.fold(ledger)
    assert set(workspace.teams) == {"team_a", "team_b"}
    lines = [a for a in workspace.anomalies if "읽지 못해" in a]
    assert len(lines) == 2, workspace.anomalies
    assert any("team_b" in line and "어긋난다" in line for line in lines), lines


def test_파일에만_들어오는_줄도_건너뛴다(tmp_path: Path):
    """DB 는 짝 없는 서로게이트를 jsonb 입력에서 거부하고 payload 를 16KB 로 묶는다(2026-09-14
    실측). 파일에는 그 문이 없다 — `EventError` 가 아닌 예외로 새면 줄 단위로 건너뛰지 못한다."""
    st = store.LocalStore(tmp_path)
    make(st)
    base = json.dumps(events.comment_posted(team_id="team_a", body="x", actor="동원",
                                            at=AT2).to_json())
    # 🔒 역슬래시는 `chr(92)` 로 만든다 — 도구 입력의 이스케이프가 실제 글자로 풀리는 함정
    surrogate = base.replace('"body": "x"', '"body": "' + chr(92) + 'ud800"')
    deep = base.replace('"body": "x"', '"body": ' + "[" * 30000 + "]" * 30000)
    (tmp_path / "events" / "surrogate.json").write_text(surrogate, encoding="utf-8")
    (tmp_path / "events" / "deep.json").write_text(deep, encoding="utf-8")

    ledger = st.read_all()
    assert [e.event_id for e in ledger.events] == [team().event_id]
    assert sorted(r.where for r in ledger.rejected) == ["events/deep.json", "events/surrogate.json"]


def test_읽지_못한_줄의_알림은_길이가_묶인다():
    """🔒 `event_id` 는 DB 에 길이 제한이 없다 — 수 KB 짜리 id 가 모든 조의 화면에 쏟아지지 않는다."""
    long_id = "x" * 2000
    row = {**team().to_json(), "event_id": long_id}
    with pytest.raises(events.EventError) as info:
        events.parse_event(row)
    rejected = events.RejectedRow.of(f"event_id {long_id!r}", row, info.value)
    line = fold.fold(events.Ledger(events=(team(),), rejected=(rejected,))).anomalies[0]
    assert "team_a" in line and len(line) < 600, len(line)
    # 🔒 규칙에 맞지 않는 조 id 는 문장에 넣지 않는다
    assert events.RejectedRow.of("x", {"team_id": "<b>"}, ValueError("y")).team_id is None


def test_못_읽은_파일은_여전히_던진다(tmp_path: Path):
    """🔴 건너뛰는 것은 **내용이 틀린 줄**뿐이다. 못 읽은 원장을 온전한 것처럼 보여주면
    무엇이 빠졌는지 아무도 모른다."""
    st = store.LocalStore(tmp_path)
    make(st)
    (tmp_path / "events" / "dir.json").mkdir()      # 읽기 자체가 실패한다
    with pytest.raises(store.StoreError, match="읽을 수 없다"):
        st.read_all()


def test_append_로는_조를_만들_수_없다(tmp_path: Path):
    """🔴 막지 않으면 **passcode 없는 조**가 생긴다 — 목록에 보이면서 아무도
    참가할 수 없는 상태다. Supabase 는 구조적으로 그렇고(해시가 먼저 있어야
    append 가 통과한다), 로컬·HF 도 같은 답을 내야 한다."""
    st = store.LocalStore(tmp_path)
    with pytest.raises(store.StoreError, match="create_team"):
        st.append([team()])
    assert st.read_all().events == ()


def test_같은_조를_두_번_만들_수_없다(tmp_path: Path):
    st = store.LocalStore(tmp_path)
    make(st)
    with pytest.raises(store.StoreError, match="이미 있다"):
        make(st, AT2)


def test_조_생성이_실패하면_해시만_남지_않는다(tmp_path: Path):
    """🔴 해시만 남으면 같은 id 로 다시 만들 수도 없는 막다른 길이 된다."""
    st = store.LocalStore(tmp_path)
    st.events_dir.mkdir(parents=True)
    # 이벤트 파일 자리를 **디렉터리**로 막아 쓰기를 실패시킨다
    (tmp_path / team().path_in_repo).mkdir()
    with pytest.raises(OSError):
        make(st)
    assert st.passcode_params("team_a") is None


def test_평문을_해시_자리에_넣을_수_없다(tmp_path: Path):
    """🔒 형식을 본다 — 규칙을 사람의 기억이 아니라 코드가 지킨다."""
    st = store.LocalStore(tmp_path)
    with pytest.raises(store.StoreError, match="해시"):
        st.create_team(team(), passcode_hash="산-바다-강-들")
    assert st.read_all().events == ()


def test_한_번에_두_조를_쓸_수_없다(tmp_path: Path):
    """🔒 자격증명은 그 조의 것이다 — `workspace_append` 가 DB 에서 막는 것과 같다."""
    st = store.LocalStore(tmp_path)
    make(st, team_id="team_a")
    make(st, AT2, team_id="team_b")
    mixed = [confirmed("steel", "a", AT2),
             events.sector_confirmed(team_id="team_b", sector_id="steel", reason="b",
                                     actor="동원", at=AT2)]
    with pytest.raises(store.StoreError, match="섞였다"):
        st.append(mixed, credential=HASH)


def test_빈_원장은_빈_목록이다(tmp_path: Path):
    assert store.LocalStore(tmp_path).read_all().events == ()


def test_워크스페이스_경로가_점수_저장소로_못_간다():
    """🔒 경로가 맞아도 **저장소가 틀리면** 막는다."""
    for path in (team().path_in_repo, store.secret_path_in_repo("team_a")):
        hub.assert_publishable_path(path, repo_id=hub.WORKSPACE_REPO_ID)
        with pytest.raises(hub.PublishBlocked):
            hub.assert_publishable_path(path, repo_id=hub.REPO_ID)


def test_조_id_가_경로로_새지_않는다():
    """🔴 이 문자열이 곧 파일 경로이고 조회 필터다."""
    for bad in ("../../etc/passwd", "team a", "Team_A", "", "a" * 41):
        with pytest.raises(events.EventError):
            store.secret_path_in_repo(bad)


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


# ── 근거 첨부 — 링크 (2026-09-14 · ADR-SC-0012) ─────────────────────────────
# 🔒 서버는 링크를 부르지 않는다. 여기서 고정하는 것은 **팀원 브라우저가 어디로 가나**와
#    **저장 모양이 멱등인가**다 — 후자에 `fold` 의 우회 차단이 기댄다.

@pytest.mark.parametrize("raw", [
    "http://dart.fss.or.kr/", "javascript:alert(1)", "data:text/html,<b>x</b>",
    "ftp://example.org/x", "file:///etc/passwd", "//naver.com/x", "naver.com/x",
])
def test_https_가_아니면_받지_않는다(raw):
    with pytest.raises(links.LinkError):
        links.normalize_link(raw)


@pytest.mark.parametrize("raw", [
    "https://127.0.0.1/", "https://[::1]/", "https://169.254.169.254/latest/meta-data",
    "https://2130706433/", "https://0x7f.1/", "https://127.1/",
    "https://localhost/", "https://intranet/", "https://nas.local/",
    "https://router.home.arpa/", "https://db.internal/", "https://printer.lan/",
    "https://%6c%6f%63%61%6c%68%6f%73%74/",
])
def test_누르는_사람의_내부망을_가리키면_받지_않는다(raw):
    """🔴 브라우저는 `0x7f.1` · `2130706433` 을 IPv4 로 읽는다. 공개 원장이 그 길을 열면 안 된다."""
    with pytest.raises(links.LinkError):
        links.normalize_link(raw)


@pytest.mark.parametrize("raw", [
    "https://naver.com@evil.example.org/", "https://user:pw@dart.fss.or.kr/",
    "https://dart.fss.or.kr:8443/", "https://dart.fss.or.kr/a b",
    "https://dart.fss.or.kr/" + chr(9) + "x", "https://dart.fss.or.kr:abc/",
    # 🔴 파이썬 IDNA(2003)가 이름을 바꾸는 것 — 브라우저는 다른 곳으로 간다 (리뷰)
    "https://faß.de/", "https://example.com" + chr(0x2024) + "evil.com/",
    "https://" + "".join(chr(0xFF00 + ord(c) - 0x20) for c in "example") + ".com/",
    # 🔴 짝 없는 서로게이트 — `LinkError` 가 아니면 fold 가 못 잡는다 (리뷰)
    "https://example.com/" + chr(0xD800),
])
def test_보이는_곳과_가는_곳이_다를_수_있으면_받지_않는다(raw):
    with pytest.raises(links.LinkError):
        links.normalize_link(raw)


def test_주소를_저장할_모양으로_굳힌다():
    assert links.normalize_link("  HTTPS://Dart.FSS.or.kr:443  ") == "https://dart.fss.or.kr/"
    assert links.normalize_link("https://example.com./") == "https://example.com/"
    # 🔒 국제화 도메인은 퓨니코드로 — 생김새가 같은 다른 글자가 드러나야 한다
    assert links.normalize_link("https://한국.kr/경로?q=값#조각") == (
        "https://xn--3e0b707e.kr/%EA%B2%BD%EB%A1%9C?q=%EA%B0%92#%EC%A1%B0%EA%B0%81")


@pytest.mark.parametrize("raw", [
    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260910000123",
    "https://한국.kr/경로?q=값#조각",
    "https://n.news.naver.com/article/001/0012345678?sid=101",
    "https://example.com/100%/a%2Fb",
    "https://Example.COM",
])
def test_굳히기가_멱등이다(raw):
    """🔒 `fold` 가 이 성질에 기대어 앱을 거치지 않고 쓴 링크를 거른다."""
    once = links.normalize_link(raw)
    assert links.normalize_link(once) == once


def test_링크는_세_개까지고_같은_링크는_하나로_센다():
    same = ["https://dart.fss.or.kr/", "HTTPS://dart.fss.or.kr:443/", "   "]
    assert links.normalize_links(same) == ("https://dart.fss.or.kr/",)
    with pytest.raises(links.LinkError, match="3개까지"):
        links.normalize_links([f"https://a{i}.example.org/" for i in range(4)])
    with pytest.raises(links.LinkError, match="목록"):
        links.normalize_links("https://dart.fss.or.kr/")


def test_링크가_너무_길면_받지_않는다():
    with pytest.raises(links.LinkError, match="너무 길다"):
        links.normalize_link("https://dart.fss.or.kr/" + "a" * links.MAX_LINK_LEN)


def test_화면에는_주소_자체가_보인다():
    """🔒 이름을 붙이게 하지 않는다 — 보이는 글자와 가는 곳이 같아야 한다."""
    assert links.display_link("https://dart.fss.or.kr/a") == "dart.fss.or.kr/a"
    long = links.display_link("https://dart.fss.or.kr/" + "a" * 100)
    assert len(long) == 60 and long.endswith("…")
    # 🔴 호스트는 자르지 않는다 — 60자에서 자르면 `naver.com` 만 보이고 진짜 도메인이 가려진다
    tricky = "n.news.naver.com.article.001.0012345678.sid.101.read.view.mobile.attacker.com"
    assert links.display_link(f"https://{tricky}/x").startswith(tricky + "/")


# ── 근거 첨부 — 코멘트 이벤트 ───────────────────────────────────────────────

def test_링크_없는_코멘트는_예전과_같은_id_다():
    """🔒 빈 `links` 칸을 넣으면 #8 이전에 쓴 코멘트와 id 가 갈라진다."""
    new = events.comment_posted(team_id="team_a", body="철강 얘기", actor="동원",
                                at=AT2, sector_id="steel")
    old = events.make_event("comment.posted", team_id="team_a", actor="동원", at=AT2,
                            payload={"body": "철강 얘기", "sector_id": "steel"})
    assert "links" not in new.payload
    assert new.event_id == old.event_id


def test_코멘트에_굳힌_링크가_실리고_왕복한다():
    event = events.comment_posted(
        team_id="team_a", body="공시 원문", actor="동원", at=AT2, sector_id="steel",
        links=["HTTPS://Dart.FSS.or.kr:443/dsaf001/main.do", "", "https://한국.kr/경로"])
    assert event.payload["links"] == ["https://dart.fss.or.kr/dsaf001/main.do",
                                      "https://xn--3e0b707e.kr/%EA%B2%BD%EB%A1%9C"]
    assert events.parse_event(json.loads(json.dumps(event.to_json()))) == event
    comment = fold.fold([team(), event]).comments_of("team_a")[0]
    assert comment.links == tuple(event.payload["links"])


def test_잘못된_링크는_이벤트가_되지_않는다():
    with pytest.raises(events.EventError, match="https"):
        events.comment_posted(team_id="team_a", body="봐라", actor="동원", at=AT2,
                              links=["http://dart.fss.or.kr/"])


def test_앱을_거치지_않고_쓴_링크는_그리지_않고_알린다():
    """🔴 passcode 를 가진 사람은 RPC 로 아무 payload 나 쓸 수 있다. **읽을 때** 거른다."""
    bypass = events.make_event("comment.posted", team_id="team_a", actor="민수", at=AT2, payload={
        "body": "봐라", "sector_id": "steel",
        "links": ["javascript:alert(1)", "HTTPS://Dart.FSS.or.kr/", "https://dart.fss.or.kr/", 7]})
    workspace = fold.fold([team(), bypass])
    assert workspace.comments_of("team_a")[0].links == ("https://dart.fss.or.kr/",)
    # 스킴 · 저장 모양 불일치 · 문자열 아님 — 셋 다 조용히 버리지 않는다
    assert len(workspace.anomalies) == 3, workspace.anomalies
    assert all("민수" in line for line in workspace.anomalies)


def test_규칙에_어긋난_섹터_id_는_반영하지_않고_알린다():
    """🔴 RPC 는 payload 를 검사하지 않는다 — 그 글자가 모달 제목 · 목록으로 흘러가면 안 된다."""
    bad = "![x](https://example.invalid/x.png)"
    confirm = events.make_event("sector.confirmed", team_id="team_a", actor="민수", at=AT2,
                                payload={"sector_id": bad, "reason": "우회"})
    comment = events.make_event("comment.posted", team_id="team_a", actor="민수", at=AT3,
                                payload={"body": "봐라", "sector_id": bad})
    workspace = fold.fold([team(), confirm, comment])
    assert not workspace.team("team_a").is_confirmed
    assert workspace.comments_of("team_a")[0].sector_id is None
    assert len(workspace.anomalies) == 2, workspace.anomalies


def test_이름_규칙을_화면이_미리_물을_수_있다():
    """🔒 세션에 담기 전에 원장과 같은 규칙으로 묻는다."""
    assert events.require_actor(" 동원 ") == "동원"
    with pytest.raises(events.EventError, match="줄바꿈"):
        events.require_actor("민" + chr(31) + "수")


def test_링크_칸이_목록이_아니면_알린다():
    bypass = events.make_event("comment.posted", team_id="team_a", actor="민수", at=AT2,
                               payload={"body": "봐라", "links": "https://dart.fss.or.kr/"})
    workspace = fold.fold([team(), bypass])
    assert workspace.comments_of("team_a")[0].links == ()
    assert any("목록" in line for line in workspace.anomalies)


@pytest.mark.parametrize("bad", [chr(0), chr(1), chr(13), chr(27), chr(127)])
def test_사람이_쓰는_글에_제어문자를_받지_않는다(bad):
    """🔴 jsonb 가 제어문자를 6바이트로 적어 DB 상한에 걸린다 — 앱이 먼저 거부한다."""
    with pytest.raises(events.EventError, match="제어문자"):
        events.comment_posted(team_id="team_a", body=f"앞{bad}뒤", actor="동원", at=AT2)
    with pytest.raises(events.EventError, match="제어문자"):
        events.sector_confirmed(team_id="team_a", sector_id="steel", reason=f"앞{bad}뒤",
                                actor="동원", at=AT2)


def test_여러_줄_사유는_받고_한_줄_칸은_줄바꿈을_받지_않는다():
    events.sector_confirmed(team_id="team_a", sector_id="steel", actor="동원", at=AT2,
                            reason="첫 줄" + chr(10) + chr(9) + "둘째 줄")
    with pytest.raises(events.EventError, match="줄바꿈"):
        events.team_created(team_id="team_a", name="A" + chr(10) + "조", actor="동원", at=AT1)
    with pytest.raises(events.EventError, match="줄바꿈"):
        events.comment_posted(team_id="team_a", body="봐라", actor="동" + chr(10) + "원", at=AT2)


def test_옛_원장의_제어문자는_읽기를_막지_않는다():
    """🔒 게이트는 쓰기 길에만 있다 — 읽기에 두면 한 줄이 원장 전체를 죽인다."""
    raw = events.make_event("comment.posted", team_id="team_a", actor="동원", at=AT2,
                            payload={"body": "앞" + chr(1) + "뒤"}).to_json()
    assert events.parse_event(raw).payload["body"] == "앞" + chr(1) + "뒤"


# ── 근거 첨부 — payload 상한 ────────────────────────────────────────────────

def test_payload_가_DB_상한을_넘으면_앱이_먼저_거부한다():
    """🔴 이모지는 한 글자가 4바이트다 — 4000자 한도 안에서도 16384바이트를 넘는다."""
    urls = [f"https://dart.fss.or.kr/{i}/" + "a" * 400 for i in range(3)]
    with pytest.raises(events.EventError, match="너무 크다"):
        events.comment_posted(team_id="team_a", body="😀" * 4000, actor="동원", at=AT2,
                              links=urls)


def test_한글_본문_4000자와_링크_셋은_상한_안에_든다():
    """🔒 쓰는 쪽 한도 안의 정상 입력은 DB 상한에 닿지 않는다 (`links.MAX_LINKS` 주석)."""
    urls = [f"https://dart.fss.or.kr/{i}/" + "a" * (links.MAX_LINK_LEN - 30)
            for i in range(links.MAX_LINKS)]
    event = events.comment_posted(team_id="team_a", body="가" * 4000, actor="동원", at=AT2,
                                  sector_id="a" * 40, links=urls)
    assert events.payload_bytes(event.payload) <= events.PAYLOAD_MAX_BYTES


def test_payload_바이트를_jsonb_텍스트_규칙으로_잰다():
    """🔒 숫자를 박아 둔다 — 식을 같은 식과 비교하면 아무것도 고정하지 않는다.

    ⚠️ PostgreSQL 에 직접 대조한 숫자는 아니다(ADR-SC-0012 ⑤). jsonb 텍스트 규칙을 옮겨 적은 것이다.
    """
    assert events.payload_bytes({"body": "가"}) == 15      # {"body": "가"} — 한글 3바이트
    assert events.payload_bytes({"body": chr(1)}) == 18    # 제어문자 한 글자가 6바이트
    assert events.payload_bytes({"a": [1, 2]}) == 13       # {"a": [1, 2]} — ", " 구분자


def test_앱의_payload_상한이_마이그레이션과_같다():
    """🔒 두 파일에 적힌 같은 수 — 한쪽만 바꾸면 여기서 깨진다."""
    sql = (Path(__file__).resolve().parents[2] / "supabase" / "migrations"
           / "20260912095328_workspace_ledger.sql").read_text(encoding="utf-8")
    assert f"octet_length(payload::text) <= {events.PAYLOAD_MAX_BYTES}" in sql


def test_원장_계층은_네트워크를_부르지_않는다():
    """🔴 "SSRF 표면 0" 은 **코드에 네트워크 호출이 없다**는 뜻이다.

    누가 링크 미리보기를 붙이면 여기서 깨진다 — 그때는 `links` 머리주석대로
    연결 시점 IP 검사를 먼저 설계한다. `store` 는 원장 저장소라 대상이 아니다.
    🔒 문자열이 아니라 **구문 트리**로 본다 — `from requests import get` · `from urllib import
       request` 도 잡는다 (문자열 검색은 그 둘을 놓쳤다 · 리뷰).
    """
    import ast

    roots = {"requests", "urllib3", "httpx", "aiohttp", "socket", "http", "huggingface_hub"}
    for module in (links, events, fold):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            else:
                continue
            bad = [n for n in names if n.split(".")[0] in roots or n.startswith("urllib.request")]
            assert not bad, f"{module.__name__} 가 네트워크 모듈을 import 한다: {bad}"


# ── 벽시계·평문 정적 검사 ───────────────────────────────────────────────────

def test_워크스페이스_계층이_벽시계를_읽지_않는다():
    """🔴 시각은 **입력**이다. 도메인이 시계를 읽으면 같은 원장이 다른 상태를 낸다."""
    for module in (events, fold, store):
        body = Path(module.__file__).read_text(encoding="utf-8").split('"""', 2)[-1]
        for banned in ("datetime.now", "time.time", "utcnow", "date.today"):
            assert banned not in body, f"`{banned}` 가 {module.__name__} 에 있다"


def test_평문_passcode_가_디스크에_없다(tmp_path: Path):
    """🔒 실제 바이트를 본다 — 계약이 아니라 결과를 확인한다.

    ★ `events/` 만 보지 않는다. 해시가 `secrets/` 로 옮겨 갔으므로 **원장 폴더
      전체**를 훑는다 — 새 파일이 생기면 자동으로 검사 범위에 든다.
    """
    secret = "산-바다-강-들"
    st = store.LocalStore(tmp_path)
    make(st, passcode_hash=auth.hash_passcode(secret))
    written = [p for p in tmp_path.rglob("*.json")]
    assert written
    for path in written:
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
        """🔒 없는 파일은 **`EntryNotFoundError`** 다 — `hub.download_bytes` 가 그것만
        `None` 으로 바꾼다. `KeyError` 를 던지면 "없음"이 사고로 올라간다."""
        import tempfile

        from huggingface_hub.errors import EntryNotFoundError

        if filename not in self.files:
            raise EntryNotFoundError(filename)
        path = Path(tempfile.mkdtemp()) / "f"
        path.write_bytes(self.files[filename])
        return str(path)


def both(tmp_path: Path) -> tuple[store.LocalStore, store.HubStore]:
    return store.LocalStore(tmp_path), store.HubStore(FakeHubApi())


def test_두_구현이_멱등에_같은_답을_낸다(tmp_path: Path):
    """🔴 실서버에서 어긋났던 지점이다.

    `HubStore` 가 이미 있는 이벤트를 그대로 커밋하면 `huggingface_hub` 가
    *"No files have been modified"* 로 건너뛰는데, 우리는 "N건 올렸다" 고 답했다.
    **0건 쓰고 N건이라 답하는 것**은 화면이 "기록됐다" 고 거짓말하게 만든다.
    """
    local, remote = both(tmp_path)
    for st in (local, remote):
        make(st)
    event = confirmed("steel", "철강 1위", AT2)

    assert local.append([event]) == remote.append([event]) == 1
    assert local.append([event]) == remote.append([event]) == 0      # ← 여기가 어긋났었다
    assert len(local.read_all().events) == len(remote.read_all().events) == 2


def test_두_구현이_자격증명에_같은_답을_낸다(tmp_path: Path):
    """🔒 로컬에서만 통과하는 코드는 배포에서 처음 깨진다."""
    local, remote = both(tmp_path)
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    for st in (local, remote):
        make(st, passcode_hash=stored)

    good = auth.recompute_passcode("산-바다-강-들", auth.params_of(stored))
    bad = auth.recompute_passcode("산-바다-강-숲", auth.params_of(stored))
    event = confirmed("steel", "철강 1위", AT2)

    for st in (local, remote):
        assert st.passcode_params("team_a") == auth.params_of(stored)
        assert st.passcode_params("team_zzz") is None
        assert st.verify("team_a", good) is True
        assert st.verify("team_a", bad) is False
        assert st.verify("team_zzz", good) is False
        with pytest.raises(store.PasscodeRejected):
            st.append([event], credential=bad)
        assert st.append([event], credential=good) == 1


def test_저장된_해시가_원장에_없다(tmp_path: Path):
    """🔴 이 세션의 요점이다 — 원장 읽기가 공개여도 해시는 나가지 않는다."""
    local, remote = both(tmp_path)
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    digest = stored.split("$")[5]
    for st in (local, remote):
        make(st, passcode_hash=stored)
        for event in st.read_all().events:
            assert "passcode_hash" not in event.payload
            assert digest not in events.canonical_json(event.to_json())
        # 🔒 앱이 받는 것에도 digest 가 없다
        assert digest not in (st.passcode_params("team_a") or "")


def test_올릴_것이_없으면_커밋하지_않는다():
    """빈 커밋은 원장에 잡음만 남긴다."""
    api = FakeHubApi()
    remote = store.HubStore(api)
    make(remote)
    assert api.commits == 1
    event = confirmed("steel", "철강 1위", AT2)
    remote.append([event])
    assert api.commits == 2
    remote.append([event])
    assert api.commits == 2          # 늘지 않는다


def test_HubStore_가_조_생성을_한_커밋으로_올린다():
    """🔒 이벤트와 해시가 갈라지면 '참가할 수 없는 조' 나 '조 없는 passcode' 가 남는다."""
    api = FakeHubApi()
    make(store.HubStore(api))
    assert api.commits == 1
    assert sorted(api.files) == [team().path_in_repo, "secrets/team_a.json"]


def test_HubStore_도_시간순으로_돌려준다():
    remote = store.HubStore(FakeHubApi())
    make(remote, AT3, team_id="team_c")
    make(remote, AT1, team_id="team_a")
    make(remote, AT2, team_id="team_b")
    assert [e.at for e in remote.read_all().events] == [AT1, AT2, AT3]


def test_목록을_못_받으면_던진다():
    class Broken(FakeHubApi):
        def list_repo_files(self, **_: object):
            raise ConnectionError("끊김")

    with pytest.raises(store.StoreError, match="파일 목록"):
        store.HubStore(Broken()).read_all().events


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


# ── Supabase — 스키마 계약 ──────────────────────────────────────────────────
# 🔒 로컬에 `SUPABASE_*` 시크릿이 없어도(2026-09-12 현재 없다) 계약은 검증된다.
#    `HubStore` 에 가짜 API 를 주는 것과 같은 방식이다 — 네트워크를 부르지 않는다.
# 🔴 실동작 검증은 ADR-SC-0011 "적용" 절에서 **anon 역할로 DB 에 직접** 11항목을
#    돌렸다(V39). 여기서 고정하는 것은 **클라이언트가 그 계약을 지키는가** 다.


class FakeResponse:
    """`requests.Response` 가 `SupabaseStore` 에게 보이는 만큼만."""

    def __init__(self, status_code: int, body: object = None) -> None:
        self.status_code = status_code
        self._body = body
        self.text = "" if body is None else json.dumps(body, ensure_ascii=False)

    def json(self) -> object:
        if self._body is None:
            raise ValueError("본문이 없다")
        return self._body


class FakeUniqueViolation(Exception):
    """🔒 부분 유니크 인덱스 위반. 실제로는 PostgREST 가 영문 제약 이름을 실어 보낸다 —
    그래서 함수 쪽에 사람이 읽을 수 있는 거절을 따로 두었다(`20260917053600` ②)."""


class FakePostgrest:
    """마이그레이션들의 계약을 흉내낸다 — `20260912095328` + `20260917053500`/`053600`.

    🔴 **가짜가 진짜보다 관대하면 안 된다.** 관대하면 `store._reject_team_created` 를
       지워도 테스트가 안 깨지고, 그 순간 원장 위조가 CI 를 통과한다. 그래서 여기
       세 가지를 진짜와 같이 막는다 — append 의 `team.created` 거절 · 조마다
       `team.created` 하나(부분 유니크 인덱스) · 0건 삽입 시 조 생성 실패.

    🔒 빈 배열을 passcode 검사 앞에서 0 으로 돌려보내는 것도 그대로 둔다. 그 성질
       때문에 빈 append 로는 검증할 수 없고, 그래서 `workspace_verify_passcode` 가 있다.
    """

    URL = "https://fake.supabase.co"
    KEY = "sb_publishable_fake"

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.secrets: dict[str, str] = {}
        self.publish: dict[str, object] = {}
        self.calls: list[str] = []

    # 쓰기 ─────────────────────────────────────────────────────────────────

    def post(self, url, *, json=None, headers=None, timeout=None):   # noqa: A002
        assert headers["apikey"] == self.KEY, "키를 헤더에 싣지 않았다"
        assert headers["Authorization"] == f"Bearer {self.KEY}"
        name = url.rsplit("/", 1)[-1]
        self.calls.append(name)
        handler = getattr(self, f"_rpc_{name}", None)
        if handler is None:
            return FakeResponse(404, {"message": f"함수가 없다: {name}"})
        try:
            return handler(json or {})
        except FakeUniqueViolation as exc:
            return FakeResponse(409, {"message": str(exc)})

    def _rpc_workspace_create_team(self, p):
        event, hashed = p["p_event"], p["p_passcode_hash"]
        if event.get("kind") != "team.created":
            return FakeResponse(400, {"message": "조 생성 RPC 는 team.created 만 받는다"})
        if not str(hashed).startswith("scrypt$"):
            return FakeResponse(400, {"message": "passcode 는 해시로만 받는다"})
        team_id = event["team_id"]
        if team_id in self.secrets:
            return FakeResponse(400, {"message": f"조 {team_id} 는 이미 있다"})
        # 🔴 orphan — 시크릿은 없는데 원장에 생성 기록이 있다 (`20260917053600` ②')
        if self._created_rows(team_id):
            return FakeResponse(400, {
                "message": f"조 {team_id} 의 생성 기록이 이미 원장에 있다"})
        self.secrets[team_id] = hashed
        try:
            written = self._insert(event)
        except FakeUniqueViolation:
            # 🔒 진짜는 **한 트랜잭션**이라 시크릿도 남지 않는다. 지금은 위 orphan
            #    검사 때문에 닿지 않지만, 그 검사를 지우는 순간 가짜가 진짜보다
            #    관대해진다 — 그 길을 미리 막는다
            del self.secrets[team_id]
            raise
        if written == 0:
            # 🔴 event_id 선점 — 옛 정의는 여기서 조용히 1 을 돌려줬다.
            #    한 트랜잭션이므로 시크릿도 남지 않는다.
            del self.secrets[team_id]
            return FakeResponse(400, {
                "message": f"조 {team_id} 의 생성 기록을 쓰지 못했다"})
        return FakeResponse(200, written)

    def _rpc_workspace_verify_passcode(self, p):
        """🔒 참·거짓만. 없는 조와 틀린 passcode 가 **같은 값**이다 (`20260917053500`)."""
        stored = self.secrets.get(p["p_team_id"])
        return FakeResponse(200, stored is not None and stored == p.get("p_encoded"))

    def _rpc_workspace_passcode_params(self, p):
        stored = self.secrets.get(p["p_team_id"])
        return FakeResponse(200, None if stored is None else auth.params_of(stored))

    def _rpc_workspace_append(self, p):
        rows = p["p_events"]
        if not isinstance(rows, list):
            return FakeResponse(400, {"message": "이벤트는 배열로 준다"})
        if not rows:
            return FakeResponse(200, 0)     # 🔴 passcode 검사 **앞**이다 (머리주석)
        if len(rows) > 50:
            return FakeResponse(400, {"message": "한 번에 보낼 수 있는 이벤트는 50건까지다"})
        # 🔴 조 생성은 `workspace_create_team` 만 한다 (`20260917053600` ②).
        #    🔒 passcode 검사 **앞**이다 — 종류 오류가 "틀린 passcode" 로 묻히면 안 된다
        if any(r.get("kind") == "team.created" for r in rows):
            return FakeResponse(400, {
                "message": "조 생성은 append 로 하지 않는다 — workspace_create_team 을 쓴다"})
        team_id = p["p_team_id"]
        stored = self.secrets.get(team_id)
        if stored is None or stored != p.get("p_encoded"):
            return FakeResponse(400, {"code": "P0001", "message": store.REJECTED_MESSAGE})
        bad = sorted({r["team_id"] for r in rows if r["team_id"] != team_id})
        if bad:
            return FakeResponse(400, {"message": f"다른 조의 이벤트가 섞였다: {', '.join(bad)}"})
        return FakeResponse(200, sum(self._insert(row) for row in rows))

    def _rpc_workspace_heartbeat(self, p):
        bas_dd = str(p["p_bas_dd"])
        if len(bas_dd) != 8 or not bas_dd.isdigit():
            return FakeResponse(400, {"message": "bas_dd 는 YYYYMMDD 여야 한다"})
        self.publish[bas_dd] = p.get("p_note")
        return FakeResponse(204)            # void — 본문이 없다

    def _created_rows(self, team_id: str) -> list[dict]:
        return [r for r in self.rows.values()
                if r["team_id"] == team_id and r["kind"] == "team.created"]

    def _insert(self, row) -> int:
        # 🔒 DB 의 `check (not (payload ? 'passcode_hash'))` 를 픽스처도 갖는다
        assert "passcode_hash" not in (row.get("payload") or {}), "해시가 원장으로 갔다"
        if row["event_id"] in self.rows:
            return 0                        # on conflict (event_id) do nothing
        # 🔒 부분 유니크 인덱스 — 한 조에 `team.created` 는 하나 (`20260917053600` ①).
        # 🔴 arbiter 가 `event_id` 이므로 **다른** 인덱스 위반은 `do nothing` 이
        #    삼키지 않고 에러가 된다. 위 두 RPC 규칙이 둘 다 지워져야 여기까지 온다 —
        #    그때 조용히 통과하지 않게 백스톱을 둔다.
        if row["kind"] == "team.created" and self._created_rows(row["team_id"]):
            raise FakeUniqueViolation(
                f'duplicate key value violates unique constraint '
                f'"workspace_event_one_created_per_team" ({row["team_id"]})')
        self.rows[row["event_id"]] = row
        return 1

    # 읽기 ─────────────────────────────────────────────────────────────────

    def get(self, url, *, params=None, headers=None, timeout=None):
        assert headers["apikey"] == self.KEY
        assert params["order"] == "at.asc,event_id.asc", "페이지 경계가 흔들린다"
        rows = sorted(self.rows.values(), key=lambda r: (r["at"], r["event_id"]))
        for field in ("team_id", "kind"):
            want = params.get(field)
            if want:
                rows = [r for r in rows if r[field] == want.split("eq.", 1)[1]]
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 1000))
        return FakeResponse(200, rows[offset:offset + limit])


def supabase(fake: FakePostgrest | None = None):
    fake = fake if fake is not None else FakePostgrest()
    return store.SupabaseStore(url=FakePostgrest.URL, key=FakePostgrest.KEY,
                               transport=fake), fake


def test_Supabase_도_같은_계약을_지킨다():
    """🔒 화면은 저장소가 어느 쪽인지 몰라야 한다."""
    st, fake = supabase()
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    assert st.create_team(team(), passcode_hash=stored) == 1

    good = auth.recompute_passcode("산-바다-강-들", st.passcode_params("team_a"))
    assert good == stored
    assert st.passcode_params("team_zzz") is None
    assert st.verify("team_a", good) is True
    assert st.verify("team_a", stored[:-1] + "x") is False
    assert st.verify("team_zzz", good) is False

    event = confirmed("steel", "철강 1위", AT2)
    assert st.append([event], credential=good) == 1
    assert st.append([event], credential=good) == 0        # 🔒 멱등
    assert [e.at for e in st.read_all().events] == [AT1, AT2]
    assert "passcode_hash" not in fake.rows[team().event_id]["payload"]


def test_Supabase_검증이_원장을_늘리지_않는다():
    """🔒 `verify` 는 **쓰기 경로를 아예 지나지 않는다** (2026-09-17 · ADR-SC-0011 ⑭).

    옛 경로는 이미 있는 이벤트를 되보내 0건 쓰기로 확인했다. 그것은 `workspace_append`
    가 `team.created` 를 받아 준다는 데 기대고 있었고, 그 한 줄이 곧 원장 위조
    경로였다. 이제 `workspace_verify_passcode` 가 참·거짓만 돌려준다.
    """
    st, fake = supabase()
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    st.create_team(team(), passcode_hash=stored)
    before = dict(fake.rows)
    for _ in range(3):
        assert st.verify("team_a", stored) is True
    assert fake.rows == before


def test_Supabase_는_자격증명_없이_쓰지_않는다():
    """🔴 마스터 경로가 DB 에 없다 — 조용히 우회하지 않고 무엇을 해야 하는지 말한다."""
    st, fake = supabase()
    st.create_team(team(), passcode_hash=HASH)
    for credential in (None, ""):
        with pytest.raises(store.StoreError, match="passcode"):
            st.append([confirmed("steel", "철강 1위", AT2)], credential=credential)
    assert len(fake.rows) == 1          # 🔒 한 건도 들어가지 않았다


def test_Supabase_가_거부를_통신_오류와_구별한다():
    """🔴 네트워크 문제를 'passcode 가 틀렸다' 로 바꿔 보여주면 원인을 못 찾는다."""
    st, fake = supabase()
    st.create_team(team(), passcode_hash=HASH)
    event = confirmed("steel", "철강 1위", AT2)

    with pytest.raises(store.PasscodeRejected):
        st.append([event], credential="scrypt$16384$8$1$c2FsdA$dGxpbnI")

    class Down(FakePostgrest):
        def post(self, url, **kw):
            return FakeResponse(503, {"message": "service unavailable"})

    broken, _ = supabase(Down())
    with pytest.raises(store.StoreError, match="503") as excinfo:
        broken.append([event], credential=HASH)
    assert not isinstance(excinfo.value, store.PasscodeRejected)


def test_Supabase_가_digest_를_받으면_던진다():
    """🔴 RPC 가 해시를 통째로 주기 시작했다는 뜻이다 — ④ 가 깨진 상태다."""
    class Leaky(FakePostgrest):
        def _rpc_workspace_passcode_params(self, p):
            return FakeResponse(200, self.secrets.get(p["p_team_id"]))

    st, _ = supabase(Leaky())
    st.create_team(team(), passcode_hash=HASH)
    with pytest.raises(store.StoreError, match="digest"):
        st.passcode_params("team_a")


def test_Supabase_가_원장을_페이지로_끝까지_읽는다(monkeypatch):
    """🔒 PostgREST 는 한 번에 다 주지 않는다. 한 페이지만 읽으면 조가 사라진다."""
    monkeypatch.setattr(store, "_PAGE", 2)
    st, _ = supabase()
    st.create_team(team(), passcode_hash=HASH)
    ats = ["2026-09-12T0%d:00:00+00:00" % n for n in range(1, 6)]
    st.append([events.comment_posted(team_id="team_a", body=f"글 {n}", actor="동원", at=at)
               for n, at in enumerate(ats)], credential=HASH)
    assert len(st.read_all().events) == 6


def test_Supabase_는_원장이_상한을_넘으면_던진다(monkeypatch):
    """🔴 조용히 자르지 않는다 — 잘린 원장은 '가끔 조가 사라진다' 다."""
    monkeypatch.setattr(store, "_PAGE", 2)
    monkeypatch.setattr(store, "_MAX_EVENTS", 2)
    st, _ = supabase()
    st.create_team(team(), passcode_hash=HASH)
    st.append([events.comment_posted(team_id="team_a", body=f"글 {n}", actor="동원",
                                     at="2026-09-12T0%d:00:00+00:00" % n)
               for n in range(1, 4)], credential=HASH)
    with pytest.raises(store.StoreError, match="조용히 자르지 않는다"):
        st.read_all().events


def _hostile_rows() -> list[dict]:
    """🔴 DB CHECK 는 통과하지만 `parse_event` 가 거부하는 줄 — RPC 로 실제로 쓸 수 있는 모양이다."""
    base = events.comment_posted(team_id="team_a", body="원래 본문", actor="동원", at=AT2).to_json()
    return [
        {**base, "payload": {"body": "id 와 어긋난 본문"}},                   # id 가 내용과 어긋난다
        {**base, "event_id": base["event_id"] + "-p", "payload": "문자열"},  # payload 가 객체가 아니다
        {**base, "event_id": base["event_id"] + "-a", "actor": "   "},       # DB 는 길이만 본다
    ]


def test_세_구현이_읽지_못한_줄에_같은_답을_낸다(tmp_path: Path):
    """🔒 화면은 저장소가 어느 쪽인지 모른다 — 건너뛰는 것도 알리는 것도 같아야 한다."""
    local, remote = both(tmp_path)
    remote_db, fake = supabase()
    for st in (local, remote, remote_db):
        make(st)
    for row in _hostile_rows():
        body = json.dumps(row, ensure_ascii=False).encode("utf-8")
        (tmp_path / "events" / f"{row['event_id']}.json").write_bytes(body)
        remote.api.files[f"events/{row['event_id']}.json"] = body
        fake.rows[row["event_id"]] = row           # `workspace_append` 가 받아 준 것과 같다

    answers = []
    for st in (local, remote, remote_db):
        ledger = st.read_all()                     # 🔒 던지지 않는다
        answers.append(([e.event_id for e in ledger.events],
                        sorted((r.team_id, r.reason) for r in ledger.rejected)))
    assert answers[0] == answers[1] == answers[2]
    assert answers[0][0] == [team().event_id]
    assert len(answers[0][1]) == 3


def test_Supabase_는_원장에_닿지_못하면_여전히_던진다():
    """🔴 건너뛰는 것은 줄이다. 원장 자체를 못 받으면 빈 원장이 아니라 오류다."""
    class Down(FakePostgrest):
        def get(self, url, **kw):
            return FakeResponse(503, {"message": "service unavailable"})

    broken, _ = supabase(Down())
    with pytest.raises(store.StoreError, match="503"):
        broken.read_all()


def test_Supabase_참가_검증이_가짜_조_생성_줄에_막히지_않는다():
    """🔴 첫 행만 보던 `verify` 는 `at` 이 더 이른 가짜 `team.created` 한 줄에 그 조의 참가를
    전부 막았다 — 읽지 못한 행은 건너뛰고 읽히는 행으로 확인한다."""
    st, fake = supabase()
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    st.create_team(team(AT2), passcode_hash=stored)
    forged = {**team("2026-09-12T00:00:00+00:00").to_json(), "actor": "   "}
    fake.rows[forged["event_id"]] = forged
    before = dict(fake.rows)
    assert st.verify("team_a", stored) is True
    assert st.verify("team_a", stored[:-1] + "x") is False
    assert fake.rows == before                     # 🔒 검증이 원장을 늘리지 않는다


def test_Supabase_append_가_조_생성을_거절한다():
    """🔴 원장 위험 ② — 막는 곳이 Python 뿐이었다. **RPC 는 화면을 지나지 않는다.**

    그 조 passcode 를 가진 조원이 `at` 이 더 이른 유효한 `team.created` 를 쓰면
    `fold` 가 그것을 먼저 것으로 골라 조 이름 · 만든 사람 · 만든 시각이 바뀐다.
    원장은 append-only 라 **지울 수도 없다.**
    """
    st, fake = supabase()
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    st.create_team(team(AT2), passcode_hash=stored)
    forged = events.team_created(team_id="team_a", name="가로챈조",
                                 actor="조원", at="2026-09-12T00:00:00+00:00")

    # ① 앱이 먼저 막는다 — 세 구현 공통 (`_reject_team_created`)
    with pytest.raises(store.StoreError, match="조를 만들 수 없다"):
        st.append([forged], credential=stored)

    # ② 🔴 앱을 건너뛰어도 DB 가 막는다. 이것이 이번에 닫은 구멍이다
    with pytest.raises(store.StoreError, match="append 로 하지 않는다"):
        st._rpc("workspace_append", {"p_team_id": "team_a", "p_encoded": stored,
                                     "p_events": [forged.to_json()]})

    # 🔒 조의 정체가 그대로다
    team_a = fold.fold(st.read_all()).team("team_a")
    assert (team_a.name, team_a.created_by, team_a.created_at) == ("A조", "동원", AT2)


def test_Supabase_참가_검증이_읽히는_생성_기록이_없으면_거절한다():
    """🔒 **회귀가 아니라 가드다** — 옛 `verify` 도 이 성질을 갖고 있었다.

    ⑭ 로 `verify` 를 다시 쓰면서 "RPC 하나면 되는데 GET 은 왜 하나" 로 줄이고 싶어지는
    자리가 생겼다. 그때 조용히 열리는 것이 여기다.

    시크릿은 남아 있으므로 passcode 는 **맞다**. 그런데 원장의 생성 기록이 전부
    읽히지 않으면 `fold` 에 그 조가 없다 — True 를 주면 화면은 "참가했다" 고 말한 뒤
    아무 데도 들여보내지 못하고, 이후 쓰기는 전부 "없는 조" 이상이 된다.
    """
    st, fake = supabase()
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    st.create_team(team(), passcode_hash=stored)
    assert st.verify("team_a", stored) is True          # 🔒 먼저 참인 것을 확인한다

    # 생성 기록을 읽지 못하게 만든다 — id 는 내용의 해시라 한 칸만 바꿔도 어긋난다
    only = next(iter(fake.rows))
    fake.rows[only] = {**fake.rows[only], "actor": "   "}

    assert fake.secrets["team_a"] == stored             # 🔒 passcode 는 그대로 맞다
    assert st.verify("team_a", stored) is False


def test_Supabase_조_생성이_조용히_성공하지_않는다():
    """🔴 옛 정의는 `on conflict do nothing` 뒤에 무조건 `return 1` 이었다.

    공격자가 그 `event_id` 를 **다른 종류**로 먼저 차지하면(부분 유니크 인덱스에도
    걸리지 않는다) 진짜 조 생성이 0건으로 삼켜지는데 화면은 "조를 만들었다" 를
    띄우고 세션까지 묶는다. 시크릿은 들어갔으므로 같은 id 로 다시 만들 수도 없다.
    """
    st, fake = supabase()
    target = team(team_id="team_b").event_id
    fake.rows[target] = {"event_id": target, "kind": "comment.posted",
                         "team_id": "team_z", "actor": "공격자", "at": AT1, "payload": {}}

    with pytest.raises(store.StoreError, match="쓰지 못했다"):
        st.create_team(team(team_id="team_b"), passcode_hash=HASH)
    # 🔒 한 트랜잭션이다 — 시크릿도 남지 않는다. 남으면 그 id 가 영구 사망한다
    assert "team_b" not in fake.secrets


def test_Supabase_가_원장의_생성_기록_위에_조를_다시_만들지_않는다():
    """🔴 orphan — 시크릿은 없는데 원장에는 생성 기록이 있다 (옛 원장 backfill 등).

    인덱스가 생기면 여기서 영문 제약 이름이 화면에 그대로 뜬다. 함수가 먼저
    사람이 읽을 수 있는 문장을 준다 — 🔒 마스터 RPC 가 없으므로 줄 수 있는 다음
    할 일은 "다른 id" 뿐이고, 그 사실까지 문장에 있다.
    """
    st, fake = supabase()
    created = team()
    fake.rows[created.event_id] = created.to_json()      # 시크릿 없이 원장에만
    with pytest.raises(store.StoreError, match="이미 원장에 있다"):
        st.create_team(created, passcode_hash=HASH)
    assert "team_a" not in fake.secrets


def test_Supabase_검증이_실패에_열리지_않는다():
    """🔴 ADR-SC-0011 ⑭ 의 fail-closed 주장을 **테스트로** 못 박는다.

    주장만 적어 두면 다음 사람이 `except` 로 감싸 "확인할 수 없으니 통과" 로 바꾼다.
    세 가지 실패를 각각 본다 — 어느 것도 참가를 열지 않아야 한다.
    """
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)

    # ① 새 앱 + 옛 DB — 마이그레이션 전에 배포했다. 검증 RPC 가 아직 없다
    class BeforeMigration(FakePostgrest):
        _rpc_workspace_verify_passcode = None

    st, _ = supabase(BeforeMigration())
    st.create_team(team(), passcode_hash=stored)
    with pytest.raises(store.StoreError, match="함수가 없다"):
        st.verify("team_a", stored)

    # ② 원장을 못 읽는다 — 빈 원장으로 보고 통과시키지 않는다
    class Down(FakePostgrest):
        def get(self, url, **kw):
            return FakeResponse(503, {"message": "service unavailable"})

    st, _ = supabase(Down())
    with pytest.raises(store.StoreError, match="503"):
        st.verify("team_a", stored)

    # ③ RPC 가 `null` 을 실어 보낸다 — **모르는 답을 통과로 읽지 않는다**
    class Null(FakePostgrest):
        def _rpc_workspace_verify_passcode(self, p):
            return FakeResponse(200, None)

    st, _ = supabase(Null())
    st.create_team(team(), passcode_hash=stored)
    assert st.verify("team_a", stored) is False


def test_Supabase_검증이_조_id_를_두_호출에_같게_보낸다():
    """🔴 옛 코드는 GET 에만 `require_team_id` 를 통과시키고 RPC 에는 원본을 보냈다.

    결과가 fail-closed 라 사고로 드러나지 않았을 뿐, **두 호출이 다른 조를 가리켰다.**
    """
    st, fake = supabase()
    stored = auth.hash_passcode("산-바다-강-들", salt=b"0" * 16)
    st.create_team(team(), passcode_hash=stored)
    seen: list[str] = []
    original = fake._rpc_workspace_verify_passcode

    def spy(p):
        seen.append(p["p_team_id"])
        return original(p)

    fake._rpc_workspace_verify_passcode = spy
    assert st.verify("  team_a  ", stored) is True
    assert seen == ["team_a"], "RPC 가 다듬지 않은 id 를 받았다"


def test_Supabase_하트비트가_void_를_받아낸다():
    """🔒 7일 pause 를 이것으로 푼다 — 본문 없는 204 에서 죽으면 안 된다."""
    st, fake = supabase()
    assert st.heartbeat("20260910", "게시 35건") is None
    assert fake.publish == {"20260910": "게시 35건"}
    with pytest.raises(store.StoreError, match="YYYYMMDD"):
        st.heartbeat("2026-09-10")


@pytest.mark.parametrize("key", [
    "sb_secret_abcdef",
    # role=service_role 인 JWT 흉내 — 서명은 보지 않는다(서버가 본다)
    "eyJhbGciOiJIUzI1NiJ9."
    + base64.urlsafe_b64encode(b'{"role":"service_role"}').decode().rstrip("=")
    + ".sig",
])
def test_권한_큰_키를_앱에_둘_수_없다(key):
    """🔴 HF 토큰을 `WRITE`/`READ` 로 가른 것과 같은 사고를 미리 막는다(V29).

    `service_role` 은 RLS 를 통째로 우회하고 v2.0 유산 53개 테이블 전부에 닿는다.
    """
    with pytest.raises(store.StoreError, match="anon|secret"):
        store.SupabaseStore(url=FakePostgrest.URL, key=key, transport=FakePostgrest())


def test_anon_JWT_는_통과한다():
    anon = ("eyJhbGciOiJIUzI1NiJ9."
            + base64.urlsafe_b64encode(b'{"role":"anon"}').decode().rstrip("=")
            + ".sig")
    assert store.SupabaseStore(url=FakePostgrest.URL, key=anon,
                               transport=FakePostgrest())._key == anon


def test_역할을_읽을_수_없는_키는_막지_않는다():
    """🔒 모르는 새 형식을 막아 앱이 아예 못 뜨게 만드는 것이 더 나쁘다.
    우리가 막는 것은 **아는 사고**(service_role 붙여넣기)다."""
    assert store.SupabaseStore(url=FakePostgrest.URL, key="sb_publishable_xyz",
                               transport=FakePostgrest())


def test_https_가_아니면_거부한다():
    """🔒 http 면 키가 평문으로 나간다. 되돌릴 수 없는 종류의 실수다."""
    for url in ("http://fake.supabase.co", "fake.supabase.co"):
        with pytest.raises(store.StoreError, match="https"):
            store.SupabaseStore(url=url, key=FakePostgrest.KEY, transport=FakePostgrest())


def test_시크릿이_없으면_무엇을_해야_하는지_말한다(monkeypatch):
    """🔒 `secret_access` 의 규율 — 없는 것은 없다고 말하고 다음 할 일을 준다."""
    monkeypatch.setattr(store, "get_secret", lambda *a, **kw: None)
    with pytest.raises(store.StoreError, match="SUPABASE_URL"):
        store.SupabaseStore(transport=FakePostgrest())
    with pytest.raises(store.StoreError, match="anon"):
        store.SupabaseStore(url=FakePostgrest.URL, transport=FakePostgrest())


def _migrations() -> list[Path]:
    """마이그레이션 전부를 **버전 순**으로. 파일명 타임스탬프가 곧 DB 버전이다."""
    root = Path(__file__).resolve().parents[2] / "supabase" / "migrations"
    paths = sorted(root.glob("*.sql"))
    assert paths, "마이그레이션을 하나도 찾지 못했다"
    return paths


def _live_function(name: str) -> str:
    """그 함수의 **살아 있는** 정의 — 마지막으로 `create or replace` 한 것.

    🔴 파일 하나를 하드코딩하면, 다음 마이그레이션이 같은 함수를 갈아끼운 순간
       이 검사가 **죽은 파일**을 읽으며 통과한다. README 가 경고한 사고
       ("갈라지면 클라이언트가 거부를 통신 오류로 오인한다")가, 그것을 막으라고
       만든 테스트를 통과한 채로 일어난다.
    """
    marker = f"create or replace function public.{name}("
    live = None
    for path in _migrations():
        text = path.read_text(encoding="utf-8")
        start = text.rfind(marker)
        if start == -1:
            continue
        # 🔒 마커가 주석 줄에 있으면 정의가 아니다 — 슬라이스가 엉뚱해진다
        line_start = text.rfind("\n", 0, start) + 1
        assert not text[line_start:start].lstrip().startswith("--"), \
            f"{path.name} 의 {name} 마커가 주석 안에 있다"
        # 🔴 본문 시작(`as $$`)을 찾고 **그 뒤 첫 `$$;`** 로 닫는다.
        #    `end $$;` 만 찾으면 `language sql` 함수(`$$;` 로 끝난다)에서 종료를 놓쳐
        #    **다음 함수를 통째로 삼킨다** — 예외가 아니라 조용한 오답이 된다.
        body = text.find("as $$", start)
        assert body != -1, f"{path.name} 의 {name} 에 `as $$` 본문이 없다"
        end = text.find("$$;", body + len("as $$"))
        assert end != -1, f"{path.name} 의 {name} 정의가 닫히지 않았다"
        live = text[start:end + len("$$;")]
    assert live is not None, f"{name} 정의를 마이그레이션에서 찾지 못했다"
    return live


def test_살아있는_정의_헬퍼가_다음_함수를_삼키지_않는다():
    """🔴 `language sql` 함수는 `$$;` 로 끝난다 — `end $$;` 만 찾으면 못 닫는다.

    이 헬퍼가 조용히 틀리면 아래 대조들이 **엉뚱한 함수를 검사하며 통과**한다.
    """
    for name in ("workspace_ct_eq", "workspace_passcode_params",       # language sql
                 "workspace_append", "workspace_create_team",          # language plpgsql
                 "workspace_verify_passcode"):
        live = _live_function(name)
        assert live.startswith(f"create or replace function public.{name}(")
        assert live.endswith("$$;")
        assert live.count("create or replace function") == 1, f"{name} 이 다음 함수를 삼켰다"


def test_거부_문장이_마이그레이션과_같다():
    """🔴 갈라지면 **거부를 통신 오류로 오인**해 화면이 엉뚱한 말을 한다.

    `SupabaseStore._decode` 가 PostgREST 가 실어 보낸 문장을 보고
    `PasscodeRejected` 로 올린다. 그 문장의 정본은 SQL 이다.
    """
    assert f"'{store.REJECTED_MESSAGE}'" in _live_function("workspace_append"), \
        store.REJECTED_MESSAGE


def test_원장_CHECK_와_코어가_같은_키를_막는다():
    """🔒 DB 와 코어가 같은 규칙을 갖는다 — 두 곳이 갈라지면 한쪽이 새거나 거부한다."""
    sql = "".join(path.read_text(encoding="utf-8") for path in _migrations())
    assert f"payload ? '{events.LEGACY_SECRET_KEY}'" in sql


def test_마이그레이션이_조_생성을_append_에서_막는다():
    """🔴 원장 위험 ② — 막는 곳이 Python 뿐이면 RPC 가 앱을 건너뛴다."""
    live = _live_function("workspace_append")
    assert "'team.created'" in live, "append 가 조 생성을 거르지 않는다"
    # 🔒 passcode 검사 **앞**이어야 한다 — 종류 오류가 "틀린 passcode" 로 묻히지 않게
    assert live.index("'team.created'") < live.index(store.REJECTED_MESSAGE)


def test_마이그레이션이_조_생성의_두_실패를_말한다():
    """🔴 앵커가 없으면 `FakePostgrest` 만 그 성질을 붙잡는다.

    가짜는 이 커밋에서 손으로 고친 것이라, SQL 쪽 `create_team` 을 옛 정의로
    되돌려도 테스트가 **전부 통과**한다 — 실제로 그랬다. 진짜를 묶는다.
    """
    live = _live_function("workspace_create_team")
    # orphan — 원장에 생성 기록이 있는데 시크릿이 없다
    assert "이미 원장에 있다" in live
    assert "kind = 'team.created'" in live
    # 0건 삽입 — 옛 정의는 `on conflict do nothing` 뒤에 무조건 `return 1` 이었다
    assert "v_written = 0" in live
    assert "return 1;" not in live, "조 생성이 다시 무조건 1 을 돌려준다"


def test_마이그레이션이_조마다_생성_기록_하나를_강제한다():
    """🔒 규칙은 함수에, 불변식은 인덱스에. 함수는 갈아끼울 수 있고 인덱스는 남는다."""
    sql = "".join(path.read_text(encoding="utf-8") for path in _migrations())
    assert "unique index if not exists workspace_event_one_created_per_team" in sql
    assert "where kind = 'team.created'" in sql


def test_검증_RPC_가_stable_이_아니다():
    """🔴 PostgREST 는 STABLE 함수를 **GET 으로도** 노출한다.

    `p_encoded` 는 저장된 해시 그 자체 — 곧 그 조의 영구 쓰기 자격증명이다.
    STABLE 이 되는 순간 그것이 쿼리스트링에 실려 로그·프록시·브라우저 히스토리에
    남고, passcode 를 바꾸는 RPC 가 없으므로 **회수할 수 없다.**
    """
    head = _live_function("workspace_verify_passcode").split("as $$")[0]
    # 🔒 주석을 걷어낸 뒤 본다 — 머리에 "STABLE 로 바꾸지 마라" 가 적혀 있어서,
    #    문자열만 보면 그 경고문 자체에 걸린다
    code = "\n".join(line.split("--")[0] for line in head.splitlines()).lower()
    assert "volatile" in code, "검증 RPC 가 volatile 이 아니다"
    assert "stable" not in code and "immutable" not in code
