"""화면 계층의 스냅샷 장치 — `sector/conftest.py` 의 `Snapshot` 을 **그대로** 쓰고 폴더만 가른다.

🔒 장치를 두 벌 만들지 않는다. JSON 모양(`to_json_safe`)과 "없는 스냅샷은 통과가 아니다" 가
   한 곳에서만 정해져야 두 계층의 골든이 같은 규칙을 따른다.

🔒 `pytest_addoption` 은 여기서 **다시 등록하지 않는다** — 같은 옵션을 두 번 등록하면 pytest 가
   시작부터 죽는다. `--snapshot-update` 는 `sector/conftest.py` 가 등록하고, 루트 `pytest.ini` 의
   `testpaths` 가 그 파일을 먼저 읽는다. `pytest dashboard` 만 돌리면 옵션이 없어 대조로만 돈다 —
   갱신은 `pytest sector dashboard --snapshot-update` 로 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sector.conftest import Snapshot

GOLDEN_DIR = Path(__file__).parent / "agent" / "testdata" / "golden"


@pytest.fixture
def snapshot(request: pytest.FixtureRequest) -> Snapshot:
    update = bool(request.config.getoption("--snapshot-update", default=False))
    return Snapshot(request.node.name, update=update, golden_dir=GOLDEN_DIR)
