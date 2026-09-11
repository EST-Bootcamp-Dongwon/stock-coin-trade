"""`batch/publish.py` 테스트 — **두 번째 실행에서 0건**을 증명한다.

이 파일의 중심은 CLI 왕복 시험이다. 가짜 HF 에 실제로 커밋하고, 같은 명령을
다시 돌려 **업로드가 0건**임을 본다. 네트워크를 쓰지 않고도 M6 의 완료 조건을
그대로 시험할 수 있다.

🔒 합성 데이터만 쓴다 (제약 10).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest
from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError

from batch import publish
from sector.datastore import gate, hub

DAYS = ["20260901", "20260902", "20260903"]      # 화·수·목
SECTORS = ["alpha", "beta"]


CONFIG = SimpleNamespace(version="test-1", config_sha256="c" * 64)


# ── 합성 원천 ───────────────────────────────────────────────────────────────

def derived_frames(days=DAYS, *, shares_step=100, stamp="2026-09-11T06:00:00+00:00",
                   idx_bump=0):
    sector_rows, market_rows, score_rows = [], [], []
    for step, day in enumerate(days):
        for i, sid in enumerate(SECTORS):
            sector_rows.append({
                "bas_dd": day, "sector_id": sid, "gics": "Industrials",
                "etf_n": 2, "etf_idx_bp": 10000 + step * 100 + idx_bump,
                "etf_ret_1d_bp": 100, "etf_ret_20d_bp": 500,
                "etf_shares_sum": 1000 + step * shares_step * (i + 1),
                "etf_nav_sum": 5_000_000, "etf_value_sum": 200_000_000,
                "etf_premium_bp": 12, "member_n": 5,
                "member_idx_bp": 10000 + step * 50,
                "member_ret_1d_bp": 50, "member_ret_20d_bp": 300,
                "breadth_up_bp": 6000, "breadth_n": 5,
                "source_ids": ("etf", "stk"), "is_partial": False, "fetched_at": stamp,
            })
        market_rows.append({
            "bas_dd": day, "stock_n": 2700, "mkt_idx_bp": 10000 + step * 30,
            "mkt_ret_1d_bp": 30, "mkt_ret_20d_bp": 200, "eqw_idx_bp": 10000 + step * 20,
            "eqw_ret_1d_bp": 20,
            "breadth_up_bp": 5500, "breadth_n": 2700, "is_partial": False,
            "fetched_at": stamp,
        })
        for i, sid in enumerate(SECTORS):
            # 🔒 실제 채점을 부르지 않는다 — 3영업일로는 어떤 축도 서지 않아
            #    게이트의 순위·축 검사가 아무것도 보지 못한다. 여기서 시험하려는
            #    것은 점수의 값이 아니라 **게시가 멱등한가**다.
            z = (1 - 2 * i) * 10_000 + step * 10          # alpha +1σ · beta −1σ
            score_rows.append({
                "bas_dd": day, "sector_id": sid, "gics": "Industrials",
                "m_raw_bp": 100 * (i + 1), "f_raw_bp": 200 * (i + 1),
                "b_raw_bp": 500, "v_raw_bp": -50,
                "m_z_bp": z, "f_z_bp": z, "b_z_bp": z, "v_z_bp": z,
                "n_axes_used": 4, "axes_missing": "", "axes_degraded": "",
                "score_balanced_bp": z, "rank_balanced": i + 1,
                "score_momentum_bp": z, "rank_momentum": i + 1,
                "score_contrarian_bp": z, "rank_contrarian": i + 1,
                "liquidity_ok": True, "etf_n": 2, "is_partial": False,
                "config_version": CONFIG.version, "config_sha256": CONFIG.config_sha256,
                "fetched_at": stamp,
            })

    def typed(rows):
        frame = pd.DataFrame(rows)
        return frame.astype({c: "Int64" for c in frame.columns
                             if frame[c].dtype.kind in "iu"})

    return typed(sector_rows), typed(market_rows), typed(score_rows)


def write_derived(directory, **kwargs):
    directory.mkdir(parents=True, exist_ok=True)
    sector, market, score = derived_frames(**kwargs)
    sector.to_parquet(directory / "sector_daily.parquet", index=False)
    market.to_parquet(directory / "market_daily.parquet", index=False)
    score.to_parquet(directory / "score_daily.parquet", index=False)
    return directory


def published(**kwargs):
    sector, market, score = derived_frames(**kwargs)
    return (gate.project_sector(sector), gate.project_market(market),
            gate.project_score(score))


# ── 가짜 HF — 커밋한 것을 기억한다 ──────────────────────────────────────────

class FakeHub:
    def __init__(self, *, private=True, exists=False):
        self.private = private
        self.exists = exists
        self.files: dict[str, bytes] = {}
        self.commits: list[list] = []
        self.tmp = None

    def repo_info(self, *, repo_id, repo_type):
        if not self.exists:
            import requests
            response = requests.Response()
            response.status_code = 404
            raise RepositoryNotFoundError("없다", response=response)
        return SimpleNamespace(private=self.private)

    def create_repo(self, *, repo_id, repo_type, private, exist_ok):
        self.exists, self.private = True, private

    def hf_hub_download(self, *, repo_id, repo_type, filename):
        if filename not in self.files:
            raise EntryNotFoundError("없다")
        path = self.tmp / filename.replace("/", "__")
        path.write_bytes(self.files[filename])
        return str(path)

    def create_commit(self, *, repo_id, repo_type, operations, commit_message,
                      commit_description=""):
        self.commits.append(list(operations))
        for operation in operations:
            path = operation.path_in_repo
            if type(operation).__name__ == "CommitOperationDelete":
                self.files.pop(path, None)
            else:
                self.files[path] = bytes(operation.path_or_fileobj)
        return SimpleNamespace(oid="a" * 40)

    @property
    def last_upload_count(self) -> int:
        return len(self.commits[-1]) if self.commits else 0


@pytest.fixture
def fake_hub(monkeypatch, tmp_path):
    api = FakeHub()
    api.tmp = tmp_path / "dl"
    api.tmp.mkdir()
    monkeypatch.setattr(hub, "write_token", lambda: "hf_fake")
    monkeypatch.setattr(publish.hub, "dataset_api", lambda token: api)
    return api


def run_cli(tmp_path, fake_hub, *extra, date="20260903"):
    directory = write_derived(tmp_path / "derived") if not (
        tmp_path / "derived").exists() else tmp_path / "derived"
    return publish.main(["--date", date, "--derived-dir", str(directory), *extra])


# ── 🔴 멱등성 — M6 의 완료 조건 ─────────────────────────────────────────────

def test_두_번째_실행에서_업로드가_0건이다(tmp_path, fake_hub, capsys):
    """🔴 M6 완료 조건. 같은 날짜를 두 번 게시하면 두 번째는 올릴 것이 없다."""
    assert run_cli(tmp_path, fake_hub) == 0
    first = fake_hub.last_upload_count
    assert first > 0

    assert run_cli(tmp_path, fake_hub) == 0
    assert len(fake_hub.commits) == 1, "두 번째 실행이 커밋을 하나 더 만들었다"
    assert "업로드 0건" in capsys.readouterr().out


def test_빌드_시각만_바뀌면_올리지_않는다(tmp_path, fake_hub):
    """집계를 다시 돌리면 `fetched_at` 이 바뀐다. 값이 같으면 내용은 같다."""
    write_derived(tmp_path / "derived")
    assert run_cli(tmp_path, fake_hub) == 0
    before = dict(fake_hub.files)

    write_derived(tmp_path / "derived", stamp="2026-09-12T23:59:00+00:00")
    assert run_cli(tmp_path, fake_hub) == 0
    assert len(fake_hub.commits) == 1
    assert fake_hub.files == before


def test_값이_바뀌면_올린다(tmp_path, fake_hub):
    write_derived(tmp_path / "derived")
    run_cli(tmp_path, fake_hub)
    write_derived(tmp_path / "derived", idx_bump=7)
    assert run_cli(tmp_path, fake_hub) == 0
    assert len(fake_hub.commits) == 2, "내용이 바뀌었는데 올리지 않았다"


def test_건너뛴_파일의_MANIFEST_항목을_그대로_이어받는다(tmp_path, fake_hub):
    """🔒 MANIFEST 는 **원격 바이트**를 서술해야 한다. 새로 적으면 거짓말이 된다."""
    write_derived(tmp_path / "derived")
    run_cli(tmp_path, fake_hub)
    first = json.loads(fake_hub.files[publish.MANIFEST_PATH])

    write_derived(tmp_path / "derived", stamp="2026-09-20T00:00:00+00:00", idx_bump=1)
    run_cli(tmp_path, fake_hub, date="20260903")
    second = json.loads(fake_hub.files[publish.MANIFEST_PATH])

    for path, entry in second["files"].items():
        remote_sha = publish._sha256(fake_hub.files[path])
        assert entry["sha256"] == remote_sha, f"{path} 의 MANIFEST 가 원격과 어긋난다"
    assert first["files"].keys() == second["files"].keys()


def test_같은_입력이면_같은_바이트다(tmp_path):
    sector, market, score = published()
    a = publish.build_shards(sector, market, score)
    b = publish.build_shards(sector, market, score)
    assert [(s.path, s.data) for s in a] == [(s.path, s.data) for s in b]


def test_MANIFEST_가_결정적이다():
    sector, market, score = published()
    shards = publish.build_shards(sector, market, score)
    kwargs = dict(as_of="20260903", published_date="20260903",
                  repo_id="x/y", config=CONFIG)
    one = publish.plan_publish(shards, None, None, **kwargs)
    two = publish.plan_publish(list(reversed(shards)), None, None, **kwargs)
    assert one.manifest_bytes == two.manifest_bytes


def test_계획이_원격_MANIFEST_를_보고_건너뛴다():
    sector, market, score = published()
    shards = publish.build_shards(sector, market, score)
    kwargs = dict(as_of="20260903", published_date="20260903",
                  repo_id="x/y", config=CONFIG)
    first = publish.plan_publish(shards, None, None, **kwargs)
    assert len(first.upload) == len(shards) and first.manifest_changed

    second = publish.plan_publish(shards, first.manifest, first.manifest_bytes, **kwargs)
    assert second.upload == () and second.skipped == tuple(sorted(s.path for s in shards))
    assert not second.manifest_changed


# ── 벽시계를 읽지 않는다 ────────────────────────────────────────────────────

def test_게시_경로에_벽시계가_없다():
    """🔴 멱등성의 뿌리. `now()` 가 하나라도 있으면 같은 날짜가 다른 바이트가 된다."""
    from pathlib import Path

    source = Path(publish.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]          # 머리주석의 설명 문장은 뺀다
    for banned in ("datetime.now", "time.time", "utcnow", "date.today"):
        assert banned not in body, f"`{banned}` 가 게시 경로에 있다"


def test_snapshot_이_같은_입력에_같은_값이다():
    sector, market, score = published()
    kwargs = dict(as_of="20260903", published_date="20260903",
                  config=CONFIG, latest_days=400)
    assert publish.snapshot(sector, market, score, **kwargs) == publish.snapshot(
        sector, market, score, **kwargs)


def test_snapshot_이_값을_담지_않는다():
    sector, market, score = published()
    text = json.dumps(publish.snapshot(sector, market, score, as_of="20260903",
                                       published_date="20260903", config=CONFIG,
                                       latest_days=400), ensure_ascii=False)
    assert "200000000" not in text and "10000" not in text      # 거래대금·지수값
    assert "한국거래소" in text                                   # 약관 제10조③


# ── 게이트 ──────────────────────────────────────────────────────────────────

def test_검사를_덜_돌리면_통과가_아니다(monkeypatch):
    """🔴 '0건 검사 후 위반 없음' 은 통과가 아니라 판정 불가다."""
    sector, market, score = published()
    monkeypatch.setattr(
        publish.gate, "check",
        lambda **kw: gate.GateReport(checks_run=frozenset({"rows_present"}), violations=()),
    )
    with pytest.raises(hub.PublishBlocked, match="판정 불가"):
        publish.run_gate(sector, market, score, as_of="20260903")


def test_위반이_있으면_막는다():
    sector, market, score = published()
    with pytest.raises(hub.PublishBlocked, match="게이트"):
        publish.run_gate(sector, market, score, as_of="20260902")      # 룩어헤드


def test_게이트가_막으면_커밋이_없다(tmp_path, fake_hub):
    directory = write_derived(tmp_path / "derived")
    sector = pd.read_parquet(directory / "sector_daily.parquet")
    sector.loc[0, "etf_idx_bp"] = 0                            # 지수가 0 이하
    sector.to_parquet(directory / "sector_daily.parquet", index=False)
    assert publish.main(["--date", "20260903", "--derived-dir", str(directory)]) == 1
    assert fake_hub.commits == []


def test_모든_샤드_경로가_문을_통과한다():
    """게이트가 두 겹이다 — 열(gate.py)과 경로(hub.py)."""
    sector, market, score = published()
    shards = publish.build_shards(sector, market, score)
    for shard in shards:
        hub.assert_publishable_path(shard.path)
    for path in (publish.MANIFEST_PATH, publish.README_PATH, publish.SNAPSHOT_PATH):
        hub.assert_publishable_path(path)


def test_원천_폴더를_게시하려_하면_거부한다(tmp_path, fake_hub):
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    assert publish.main(["--date", "20260903", "--derived-dir", str(raw)]) == 1
    assert fake_hub.commits == []


# ── 샤딩 ────────────────────────────────────────────────────────────────────

def test_월별_샤드의_행_합이_전체와_같다():
    sector, market, score = published(days=["20260828", "20260831", "20260901"])
    shards = publish.build_shards(sector, market, score)
    months = [s for s in shards if s.path.startswith("sector_daily/")]
    assert sum(s.rows for s in months) == len(sector)
    assert {s.path.rsplit("_", 1)[-1][:6] for s in months} == {"202608", "202609"}


def test_latest_가_최근_영업일만_담는다():
    sector, market, score = published()
    shards = publish.build_shards(sector, market, score, latest_days=2)
    latest = next(s for s in shards if s.path == "latest/sector_daily_latest.parquet")
    assert latest.rows == 2 * len(SECTORS)


def test_latest_경로가_앱이_읽는_이름과_같다():
    """snapshot.json 이 가리키는 경로와 실제 샤드 경로가 어긋나면 앱이 빈다."""
    sector, market, score = published()
    paths = {s.path for s in publish.build_shards(sector, market, score)}
    pointed = publish.snapshot(sector, market, score, as_of="20260903",
                               published_date="20260903", config=CONFIG,
                               latest_days=400)["read_this"].values()
    assert set(pointed) <= paths


# ── CLI ─────────────────────────────────────────────────────────────────────

def test_date_는_필수다():
    with pytest.raises(SystemExit):
        publish.main([])


def test_date_형식을_본다(tmp_path):
    assert publish.main(["--date", "2026-09-10", "--derived-dir", str(tmp_path)]) == 2


def test_파생값이_없으면_무엇을_돌릴지_말한다(tmp_path, capsys):
    assert publish.main(["--date", "20260903", "--derived-dir", str(tmp_path)]) == 2
    assert "build_sector_daily" in capsys.readouterr().err


def test_dry_run_은_아무것도_올리지_않는다(tmp_path, fake_hub, capsys):
    assert run_cli(tmp_path, fake_hub, "--dry-run") == 0
    assert fake_hub.commits == [] and fake_hub.files == {}
    assert "--dry-run" in capsys.readouterr().out


def test_dry_run_은_저장소를_만들지도_않는다(tmp_path, fake_hub):
    run_cli(tmp_path, fake_hub, "--dry-run")
    assert fake_hub.exists is False


def test_저장소가_없으면_private_으로_만든다(tmp_path, fake_hub):
    assert run_cli(tmp_path, fake_hub) == 0
    assert fake_hub.exists is True and fake_hub.private is True


def test_public_이면_한_파일도_올리지_않는다(tmp_path, fake_hub, capsys):
    """🔴 M6 완료 조건 — 일부러 public 으로 두고 확인한다."""
    fake_hub.exists, fake_hub.private = True, False
    assert run_cli(tmp_path, fake_hub) == 1
    assert fake_hub.commits == [] and fake_hub.files == {}
    assert "private" in capsys.readouterr().err


def test_룩어헤드는_실리지_않는다(tmp_path, fake_hub, capsys):
    """`--date` 보다 뒤의 날은 애초에 프레임에서 잘린다."""
    assert run_cli(tmp_path, fake_hub, date="20260902") == 0
    out = capsys.readouterr().out
    assert "as_of : 20260902" in out and "영업일 2일" in out


def test_원격에만_남은_파일을_알린다(tmp_path, fake_hub, capsys):
    fake_hub.exists = True
    run_cli(tmp_path, fake_hub)
    fake_hub.files["sector_daily/year=2024/month=01/sector_daily_202401.parquet"] = b"x"
    manifest = json.loads(fake_hub.files[publish.MANIFEST_PATH])
    manifest["files"]["sector_daily/year=2024/month=01/sector_daily_202401.parquet"] = {
        "content_sha256": "z" * 64}
    fake_hub.files[publish.MANIFEST_PATH] = publish._json_bytes(manifest)

    run_cli(tmp_path, fake_hub)
    assert "원격에만 남은 파일" in capsys.readouterr().out


def test_prune_은_같은_커밋에서_지운다(tmp_path, fake_hub):
    fake_hub.exists = True
    run_cli(tmp_path, fake_hub)
    stale = "sector_daily/year=2024/month=01/sector_daily_202401.parquet"
    fake_hub.files[stale] = b"x"
    manifest = json.loads(fake_hub.files[publish.MANIFEST_PATH])
    manifest["files"][stale] = {"content_sha256": "z" * 64}
    fake_hub.files[publish.MANIFEST_PATH] = publish._json_bytes(manifest)

    run_cli(tmp_path, fake_hub, "--prune")
    assert stale not in fake_hub.files
    assert len(fake_hub.commits) == 2         # 지움도 **같은 커밋 하나**로 간다


# ── 데이터셋 카드 ───────────────────────────────────────────────────────────

def test_README_가_출처와_금지사항을_담는다():
    text = publish.readme(repo_id="x/y").decode("utf-8")
    assert "한국거래소" in text          # 약관 제10조③
    assert "제11조" in text              # 왜 원천이 없는지
    assert "투자 권유가 아니다" in text


def test_README_의_열_목록이_허용목록에서_나온다():
    """손으로 적으면 어긋난다 — 어긋난 카드는 읽는 사람을 속인다."""
    text = publish.readme(repo_id="x/y").decode("utf-8")
    for column in gate.SECTOR_PUBLISHED_COLUMNS + gate.MARKET_PUBLISHED_COLUMNS:
        assert f"`{column}`" in text
    for column in gate.DELIBERATELY_WITHHELD:
        assert f"`{column}`" in text
