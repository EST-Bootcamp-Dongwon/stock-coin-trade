# CLAUDE.md — `stock-coin-trade`

@AGENTS.md

> 위 한 줄이 이 파일의 본체다. **작업 규칙의 정본은 [`AGENTS.md`](AGENTS.md)** 이고,
> Claude Code 는 `AGENTS.md` 를 스스로 읽지 않으므로 여기서 임포트한다.
> 규칙을 바꿀 때는 **항상 `AGENTS.md` 를 먼저 고친다.**

---

## Claude Code 특화 보강

### 세션 시작 시 — 순서를 지킨다

```bash
# ① 지금 어느 계정인가 (세션 중간에도 바뀐다)
gh auth status

# ② 실제 진행 상태 — 붙여넣은 프롬프트를 그대로 믿지 않는다
git log --oneline -5
git rev-parse HEAD main origin/main
git status --short
```

그다음 [`세션-시작-프롬프트.md`](세션-시작-프롬프트.md) 를 읽는다.
**현재 마일스톤·확정 사실·다음 작업이 거기에 있다.**

### 세션 마무리 시

🔒 **다음 세션 복붙 프롬프트를 채팅으로 보여준다. 별도 파일을 만들지 않는다.**

#### ★ 이슈 수준으로 자세히 쓴다 (2026-09-17 사용자 지시)

🔴 **개조식 목록 몇 줄로 끝내지 않는다.** 다음 세션의 나는 이 프롬프트 말고는 아무것도
모르는 상태로 시작하고, 지난번에 **3세션 뒤처진 프롬프트로 착수해 이미 끝난 일을 다시 한 적이
있다.** 본보기는 [`devlee328288/Qurious#31`](https://github.com/devlee328288/Qurious/issues/31)
— 표·다이어그램·우선순위 배지로 **읽는 사람이 판단할 수 있게** 쓴 글이다.

담을 것:

| # | 무엇 | 어떻게 |
|---|---|---|
| 0 | **읽는 순서** | 맨 위 인용구 한 줄. 어디부터 읽고 무엇을 건너뛸지 |
| 1 | **쉬운 요약 3줄** | 개발 맥락을 잊은 상태에서도 읽히게. 각 줄에 링크 |
| 2 | **한눈에 보기 표** | 무엇을 했나 / 무엇이 남았나 / 우선순위 🔴🟠🟡 |
| 3 | **그림** | Mermaid `flowchart` + `subgraph`. 🔒 `C4Context`·`C4Container` 금지(GitHub 이 렌더링 못 한다) |
| 4 | **이번 세션에서 확정된 결정** | 근거·실측 수치와 함께. "왜 그렇게 정했나" 가 빠지면 다음 세션이 뒤집는다 |
| 5 | **현재 상태** | 브랜치 · 커밋 해시와 제목 · 바뀐 파일 · 테스트 건수 · 열린 이슈 번호 |
| 6 | **다음 작업 하나** | 🔒 **하나다.** 완료 조건까지 적는다 |
| 7 | **하지 말 것** | 이번에 기각한 대안과 그 이유 — 다음 세션이 같은 길을 다시 걷지 않게 |
| 8 | **참고할 경로** | 파일:줄 · ADR 번호 · 이슈 링크 |
| 9 | 🔒 **현재 `gh`·`glab` 활성 계정** | 매번 빠뜨리지 않는다 |

🔒 **숫자는 실측한 것만 적는다.** "테스트 다 통과" 가 아니라 "`pytest` 787건 ·
`backend` 23건" 이라고 적는다 — 다음 세션이 그 숫자로 회귀를 판정한다.

### 자주 쓰는 명령

```bash
# v3.0 (Streamlit) — 정본
streamlit run streamlit_app.py
pytest                                        # sector/ · batch/ 골든 수집

python -m sector.sources.krx_openapi --probe --date 20260910          # ETF 원천 점검
python -m sector.sources.krx_stock --probe --date 20260910 --market stk  # 주식 원천 점검
python -m batch.validate_config                                       # 섹터 정의 검증

# 집계 (M5) — 수집과 집계를 나눠 뒀다. 집계는 네트워크를 부르지 않는다
python -m batch.fetch_daily --days 425 --dry-run    # 무엇을 받을지만 본다
python -m batch.fetch_daily --days 425              # data/raw/ 로 수집 (멱등 · 재개 가능)
python -m batch.build_sector_daily --dry-run        # data/raw/ → 집계 (쓰지 않음)
python -m batch.build_sector_daily                  # → data/derived/*.parquet

# 채점 (M7) — 집계가 사실이면 점수는 판단이다. 그래서 단계를 나눴다
python -m batch.build_scores --dry-run              # 무엇이 나올지만 본다
python -m batch.build_scores                        # → data/derived/score_daily.parquet
pytest sector/scoring_golden_test.py                # 골든 — 값과 성질을 함께 고정
pytest sector/scoring_golden_test.py --snapshot-update   # 🔒 이유를 설명할 수 있을 때만

# 게시 (M6) — 🔒 올리는 것은 data/derived/ 의 파생값뿐. data/raw/ 는 한 파일도 안 간다
python -m batch.publish --date 20260910 --dry-run    # 무엇을 올릴지만 본다
python -m batch.publish --date 20260910              # HF private dataset 으로
python -m batch.publish --date 20260910              # ★ 두 번째 — "업로드 0건" 이어야 정상

# v2.0 (Django) — 동결. 회귀 확인용으로만 돌린다
cd backend && .venv/bin/pytest                # 골든 23건 · DB 불필요 ← 항상 통과해야 한다
docker compose --profile v2 up -d postgres
cd backend && .venv/bin/python manage.py test # DB 필요
```

⚠️ v1.0 스택(Nginx + Flask)은 이력 재시작(M1) 때 사라진다. `docker compose` 는
`--profile v2` 로만 쓴다.

### 하지 않을 것

| 금지 | 대신 |
|---|---|
| `devlee328288` 원격을 로컬에 추가 | GitLab push-mirror 로만. 로컬 경로를 만들지 않는다 |
| `gh auth status` 확인 없이 push | 작업 전마다 확인하고 보고 |
| `pykrx` 사용 | KRX Open API (`AUTH_KEY`) |
| 네이버 비공식 엔드포인트 크롤링 | KIS(준실시간) · KRX Open API(일별) |
| KRX **원천** 데이터를 HF·저장소에 업로드 | 섹터 집계·점수 등 **파생값만** |
| 값이 없을 때 0·전일값·평균으로 채우기 | 예외를 던지거나 `—` 로 표시 |
| 뉴스 요약·감성점수·"호재/악재" 라벨 | 제목·출처·시각·원문 링크만 |
| `.env`·API 키 커밋 · `.streamlit/secrets.toml` 커밋 | `.env.example` 에 키 **이름만** |
| 실제 KRX 데이터를 테스트 픽스처로 커밋 | 합성 데이터 |
| force push · `reset --hard` · 히스토리 재작성 | `git revert`. 그 외는 사용자 승인 후 |
| `gh pr merge` · auto-merge · 저장소 삭제/이관 | 사용자가 웹에서 직접 |

### 작업 흐름

🔒 **한 세션 = 한 작업.** 여러 마일스톤을 한 번에 물지 않는다 — 품질이 떨어진다.

한 작업 완료 → 검증(테스트) → 보고 → 사용자 확인 → **복붙 프롬프트 제시 후 종료.**
막히거나 확실하지 않으면 **추측하지 말고 질문한다.** 사소해 보여도 설계를 먼저 합의한다.
