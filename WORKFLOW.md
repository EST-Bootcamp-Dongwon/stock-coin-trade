# 작업 규칙 — 이 저장소는 강사님 원본의 fork 입니다

> 이 파일은 **작업본에만 있습니다.** 강사님 원본([edumgt/stock-coin-trade](https://github.com/edumgt/stock-coin-trade))에는 없습니다.

강사님이 배포하신 코인·주식 모의투자 웹 앱을 **내 개인 프로젝트로 커스터마이징**하기 위한 저장소입니다.
원본은 수업 중에도 계속 갱신되므로, 원본을 직접 고치지 않고 **fork 위에서만** 작업합니다.

## 원격 구성

| 원격 | 저장소 | 용도 |
|------|--------|------|
| `origin` | [EST-Bootcamp-Dongwon/stock-coin-trade](https://github.com/EST-Bootcamp-Dongwon/stock-coin-trade) | **내 작업본.** push 는 여기로만 |
| `upstream` | [edumgt/stock-coin-trade](https://github.com/edumgt/stock-coin-trade) | 강사 원본. **fetch 전용** — push URL 을 막아 뒀습니다 |

`upstream` 의 push URL 은 `DISABLED://` 로 설정되어 있어 실수로 원본에 push 하는 사고가
구조적으로 불가능합니다.

## 브랜치

| 브랜치 | 역할 |
|--------|------|
| `main` | **내 커스터마이징이 쌓이는 곳.** 강사님 코드를 제자리에서 고칩니다 |
| `upstream-main` | 강사님 원본 그대로의 미러. **직접 커밋하지 않습니다** |

내가 원본에서 무엇을 바꿨는지 한눈에 보려면:

```bash
git diff upstream-main main --stat
```

GitHub 웹에서도 `upstream-main` 과 `main` 을 비교하면 같은 내용을 볼 수 있습니다.

## 작업 방식 — 제자리 수정

강사님 코드 **안에서 그대로** 고칩니다. 기능 추가, UI 변경, 포트 조정 등.
새 폴더로 복사하지 않으므로 개발 흐름이 자연스러운 대신, **내가 고친 파일을 강사님도 고치면
동기화할 때 충돌**합니다. 그때의 판단 기준은 아래에 있습니다.

원본이 어땠는지는 브랜치를 옮기지 않고도 볼 수 있습니다.

```bash
git show upstream-main:python-stock-backend/app.py
```

모노레포 안에서 작업 중이라면 읽기 전용 사본이 `learning/12-stock-coin-trade/lecture/` 에도
펼쳐져 있어 파일 탐색기로 바로 비교할 수 있습니다.

```bash
diff -u learning/12-stock-coin-trade/lecture/frontend/js/stock.js \
        projects/stock-coin-trade/frontend/js/stock.js
```

## 강사님 최신 받아오기

모노레포 루트에서 스크립트로 처리합니다.

```bash
bash scripts/sync-workrepo-upstream.sh stock-coin-trade           # 차이 보고만
bash scripts/sync-workrepo-upstream.sh stock-coin-trade --merge   # sync/upstream-<날짜> 브랜치에서 merge
```

`main` 을 직접 건드리지 않고 `sync/upstream-<날짜>` 브랜치에서 merge 합니다.

이 저장소만 단독으로 clone 해서 쓰는 경우(예: WSL `~/stock-coin-trade`)에는 수동으로:

```bash
git fetch upstream
git checkout upstream-main && git merge --ff-only upstream/main   # 미러 갱신
git checkout -b sync/upstream-$(date +%F) main
git merge upstream/main
```

### 충돌이 났을 때

**자동으로 해결하지 않습니다.** 판단 기준은 하나입니다.

> **강사님 코드베이스가 기준이고, 내가 얹은 것만 지켜낸다.**

대개는 강사님 쪽을 받고(`--theirs`) 내 변경만 다시 얹으면 끝납니다.

```bash
git diff --name-only --diff-filter=U    # 충돌 파일 목록
git merge --abort                        # 포기하고 되돌리기
```

## 개발 실행

3-컨테이너(Nginx + Flask + MariaDB) 구성이라 Docker Compose 로 띄웁니다.
`docker compose` 는 현재 디렉토리의 compose 파일을 읽으므로 **이 폴더 안에서** 실행합니다.

```bash
cp .env.example .env      # 최초 1회 — 시크릿·API 키는 커밋하지 않습니다
docker compose up -d
```

WSL 에서 `/mnt/c/...` 를 Docker 에 마운트하면 I/O 가 느립니다. 컨테이너 실행 위주 작업은
WSL 파일시스템(`~/stock-coin-trade`)에 따로 clone 해서 하는 편이 빠릅니다.

## 주의사항

- **`upstream` 원격이 붙어 있으면 `gh` 가 저장소를 강사님 것으로 오인합니다.**
  PR 을 만들 때는 `--repo` 를 반드시 붙입니다.

  ```bash
  gh pr create --repo EST-Bootcamp-Dongwon/stock-coin-trade --base main --head <브랜치>
  gh repo view --json nameWithOwner -q .nameWithOwner   # 어디를 보고 있는지 확인
  ```

- **머지는 사람이 직접 합니다.** 에이전트는 PR 생성까지만 합니다.
- force push · 히스토리 재작성은 하지 않습니다.
- **`.env` · API 키 · 시크릿은 커밋하지 않습니다.** 이 저장소는 Public 입니다.
  `.env.example` 만 추적하고, 실제 값은 `.gitignore` 로 막혀 있는지 push 전에 확인합니다.
- 줄바꿈은 `.gitattributes` 로 LF 고정입니다.

자세한 규칙은 모노레포의 [`AGENTS.md`](https://github.com/EST-Bootcamp-Dongwon/EST-Camp-AI-Quant/blob/main/AGENTS.md) 2.2 · 2.4절을 참고하세요.
