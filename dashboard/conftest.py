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


@pytest.fixture(autouse=True)
def _clear_score_cache():
    """🔴 `data.load_scores` 는 `st.cache_data` 라 **pytest 세션 전체를 넘어 산다**.

    앞선 테스트가 넣어 둔 프레임을 다음 테스트가 받는데, 대개 **그래도 통과한다** —
    가장 나쁜 실패다. 실증: `_from_local` 만 가로채고 캐시를 안 비운 테스트가 자기가
    만든 합성 프레임(27행) 대신 앞 테스트의 실데이터(5985행)를 받았다(적대적 리뷰).

    🔒 규율을 **테스트마다 기억하는 것**에서 **장치가 지키는 것**으로 옮긴다.

    🔴 **캐시 함수를 이름으로 직접 잡는다.** 예전에는 `getattr(..., lambda: None)` 로
       받았는데, `load_scores` 가 검사 래퍼가 되자(이슈 #12) 그 기본값이 조용히
       이겨서 **비우는 일이 아예 일어나지 않았다.** 그러고도 대부분 통과했고 6건만
       앞 테스트의 실데이터(5985행)를 받아 엉뚱하게 실패했다 — 이 픽스처가 막으려던
       바로 그 실패다. 없는 속성은 **시끄럽게** 터지는 편이 낫다.
    """
    from dashboard import data

    clear = data._read_scores.clear
    clear()
    yield
    clear()


@pytest.fixture
def snapshot(request: pytest.FixtureRequest) -> Snapshot:
    update = bool(request.config.getoption("--snapshot-update", default=False))
    return Snapshot(request.node.name, update=update, golden_dir=GOLDEN_DIR)
