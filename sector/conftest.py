"""루트 pytest 의 **스냅샷 장치**. `sector/` 아래에서만 산다.

## 🔒 왜 여기에 있고 저장소 루트에 없는가

동결된 v2.0 은 러너가 따로다(`cd backend && .venv/bin/pytest`). 그쪽은
`backend/pytest.ini` 때문에 `rootdir=backend` 로 고정되고, pytest 는 rootdir 위로
conftest 를 찾으러 올라가지 않는다. 그래도 **루트에 두지 않는 편이 한 겹 더
안전하다** — `backend/` 는 `sector/` 를 아예 쳐다보지 않기 때문이다
(→ `AGENTS.md` 5장 · `pytest.ini` 머리주석).

## 왜 라이브러리를 쓰지 않는가

syrupy 같은 스냅샷 플러그인을 넣으면 배포 목록과 개발 목록이 또 하나 갈라지고,
pytest 메이저 버전이 오를 때마다 플러그인 호환을 따라가야 한다. 여기서 필요한 것은
"JSON 을 파일에 적고 다음번에 대조한다"가 전부라 40줄이면 된다.

## 쓰는 법

    def test_무엇(snapshot):
        snapshot.assert_match(값)            # 파일 = testdata/golden/<테스트이름>.json
        snapshot.assert_match(값, "raw")     # 한 테스트에 여러 개면 이름을 준다

    .venv/bin/python -m pytest                          # 대조
    .venv/bin/python -m pytest --snapshot-update        # 갱신

🔒 **갱신은 값이 바뀐 이유를 설명할 수 있을 때만.** 골든 먼저, 튜닝 나중이다
   (`AGENTS.md` 5장). 갱신 뒤에는 `git diff` 로 **무엇이 몇 bp 움직였는지** 본다.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

GOLDEN_DIR = Path(__file__).parent / "testdata" / "golden"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="골든 스냅샷을 현재 값으로 덮어쓴다. 값이 바뀐 이유를 설명할 수 있을 때만",
    )


def to_json_safe(value):
    """스냅샷에 들어가는 모양을 **여기 한 곳에서만** 정한다.

    🔒 `Decimal` 은 `str` 로 고정한다 — `float` 로 바꾸면 74300.0 과 74300.00000001 을
       구분하지 못하고, `repr` 은 파이썬 버전에 묶인다
       (`backend/trading/golden_test.py` 의 `_j()` 와 같은 규칙이다).
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    return value


class Snapshot:
    """한 테스트의 스냅샷 묶음."""

    def __init__(self, test_name: str, *, update: bool) -> None:
        self._test_name = test_name
        self._update = update
        self._used: set[str] = set()

    def path(self, name: str | None) -> Path:
        stem = self._test_name if name is None else f"{self._test_name}__{name}"
        return GOLDEN_DIR / f"{stem}.json"

    def assert_match(self, value, name: str | None = None) -> None:
        path = self.path(name)
        if name in self._used:
            raise AssertionError(
                f"같은 스냅샷 이름을 두 번 썼다: {path.name} — 이름을 나눠라"
            )
        self._used.add(name)  # type: ignore[arg-type]

        payload = json.dumps(
            to_json_safe(value), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"

        if self._update:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
            return

        if not path.is_file():
            # 🔴 없는 스냅샷을 "통과"로 넘기지 않는다. 그건 0건 수집과 같은 실패다.
            raise AssertionError(
                f"골든 스냅샷이 없다: {path}\n"
                f"처음이라면 `--snapshot-update` 로 만들고 **내용을 눈으로 확인한 뒤** "
                f"커밋한다."
            )
        expected = path.read_text(encoding="utf-8")
        if expected != payload:
            raise AssertionError(
                f"골든 스냅샷과 다르다: {path.name}\n"
                f"값이 바뀐 이유를 설명할 수 있으면 `--snapshot-update`.\n"
                f"--- 기대 (앞 1200자) ---\n{expected[:1200]}\n"
                f"--- 실제 (앞 1200자) ---\n{payload[:1200]}"
            )


@pytest.fixture
def snapshot(request: pytest.FixtureRequest) -> Snapshot:
    return Snapshot(request.node.name, update=request.config.getoption("--snapshot-update"))
