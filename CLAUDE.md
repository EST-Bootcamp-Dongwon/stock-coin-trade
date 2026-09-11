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
프롬프트에 담을 것:

1. 이번 세션에서 확정된 결정
2. 현재 상태 (브랜치 · 커밋 · 바뀐 파일)
3. **다음 세션에서 할 작업 하나**
4. 참고할 경로
5. 🔒 **현재 `gh` 활성 계정** — 매번 빠뜨리지 않는다

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
