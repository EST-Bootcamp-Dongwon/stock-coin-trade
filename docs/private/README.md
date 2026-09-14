# `docs/private/` — 저장소에 올리지 않는 제3자 자료

> 🔴 **이 저장소는 Public 이다.** 여기 들어오는 것은 **받아서 쓰되 재배포할 수 없는**
> 자료다. 한 번 커밋되면 이력에 남고, 이력에서 지우는 일은 force push 를 부른다.

## 규칙

| | |
|---|---|
| **커밋되는 것** | 이 `README.md` **하나뿐**이다 |
| **커밋되지 않는 것** | 이 폴더 아래의 **나머지 전부** |
| 막는 곳 | 저장소 루트 [`.gitignore`](../../.gitignore) — `/docs/private/*` + `!/docs/private/README.md` |

⚠️ 두 줄의 **순서를 뒤집지 마라.** `.gitignore` 는 마지막에 일치한 규칙이 이긴다 —
`.env*` → `!.env.example` 과 똑같은 구조다.

## 지금 들어 있는 것

### `gic/` — 가천대 GIC 프롬프트 원문 (`GIC_v16_*` 4종)

- **무엇** — 2026-09-12 에 받은 프롬프트 엔지니어링 원문. #9 **내부 에이전트 엔진
  (Layer A)** 의 설계 입력이다 (계획서 부록 C 와 1:1 로 매핑된다).
- **왜 여기인가** — 가천대 GIC 저작권물이다. 설계를 참고하는 것과 원문을 Public
  저장소에 재배포하는 것은 다른 일이다.
- 🔒 **산출물은 코드로만 남긴다.** `dashboard/agent/` 의 `intent.py` · `inventory.py` ·
  `redteam.py` · `compose.py` · `guard.py` 에 *구조*가 들어가고, 원문 문장은 들어가지 않는다
  (2026-09-14 · ADR-SC-0013). 받은 것은 **3종**이다 — 산업 Top Pick 문서는 없다.
- 🔒 **LLM 을 부르지 않는다** — 절대 제약 2(매매 신호 경로에 LLM 금지)는 그대로다.

## 확인하는 법

```bash
# README 만 추적된다
git check-ignore -v docs/private/gic/아무파일.md   # → 막힌다
git check-ignore -v docs/private/README.md         # → 출력 없음(= 추적된다)
```
