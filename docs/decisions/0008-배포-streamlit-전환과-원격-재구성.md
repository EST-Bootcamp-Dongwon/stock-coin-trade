# ADR-SC-0008 — 배포를 Streamlit Community Cloud 로 옮기고 원격을 재구성한다

- **상태**: **일부 개정됨** (① ④ ⑤ 유효 · ② ③ 은 [ADR-SC-0010](0010-두-배포-공존과-쓰기-상태-분리.md) 이 대체)
- **날짜**: 2026-09-11 (개정 2026-09-12)
- **관련**: [ADR-SC-0005](0005-산출물-재지정과-이력-재시작.md) · [ADR-SC-0006](0006-krx-데이터-제3자-제공-금지.md) · [ADR-SC-0010](0010-두-배포-공존과-쓰기-상태-분리.md)

> 🔴 **②(Vercel 연동 해제) 와 ③(Supabase 미사용) 을 근거로 삼지 마라.**
> 2026-09-12 에 전제 셋이 전부 바뀐 것이 확인됐다 — Vercel 에 Root Directory 설정이
> 있고(의존성 충돌이 풀린다), 매일 도는 배치가 Supabase 7일 pause 를 해결한다.
> **①(Streamlit 배포) ④(원격 구성) ⑤(`gh` 계정 확인) 는 그대로 유효하다.**
> → [ADR-SC-0010](0010-두-배포-공존과-쓰기-상태-분리.md)

## 맥락

**배포 쪽.** v2.0 은 Vercel 서버리스에 있다(`https://stock-coin-trade.vercel.app`).
그런데 v3.0 은 매일 데이터를 모으고 차트를 그리는 도구라 Vercel 과 맞지 않는다 —
[VERCEL.md](../../VERCEL.md) 가 이미 "무거운 배치는 번들 크기와 Active CPU 예산 때문에
안 돈다"고 적어 두었다.

2026-09-11 실측: 배포는 **빌드가 깨진 게 아니다.** `/static/css/app.css` 200,
`/admin/` 302 로 Django 는 살아 있고 `/` 만 500 이다. 원인은 **Supabase 프로젝트가
`INACTIVE`**(무료 티어 7일 무활동 자동 일시정지)다. 사용자의 Supabase 프로젝트 4개가
전부 잠들어 있다. **되살려도 7일 뒤 또 멈춘다.**

**원격 쪽.** `origin`(`EST-Bootcamp-Dongwon`, 계정 `dev-dongwon05253`)이 **Flagged** 상태다.
공개 쇼케이스는 깨끗한 `devlee328288` 계정으로 하려 하는데,
🔒 **그 저장소에 `dev-dongwon05253` 자격으로 push 하는 일은 절대 없어야 한다.**

## 결정

### ① 배포를 Streamlit Community Cloud 로 옮긴다

**Streamlit Cloud 는 GitHub 전용이다**(GitLab 배포 불가). 그래서 다음 경로가 된다.

```
GitLab (정본)  ──push mirror──▶  devlee328288 GitHub (공개)  ──▶  Streamlit Cloud
```

- **저장소는 public**(코드만 · 데이터 없음) — 쇼케이스
- **앱은 private**(뷰어 6명 이메일 지정) — [ADR-SC-0006](0006-krx-데이터-제3자-제공-금지.md) 준수
- 한도: 메모리 최대 2.7GB · 12시간 무트래픽 시 슬립 · **private 앱은 동시 1개**

### ② Vercel 연동을 해제한다. 코드는 보존한다 &nbsp;🔴 **개정됨 → ADR-SC-0010 ①③**

`api/index.py` · `vercel.json` · `backend/` 를 지우지 않는다.
Vercel 에서 **Git 연동만 끊는다.** 되살리고 싶으면 연동을 다시 걸면 된다.

근거 세 가지:
1. 깨진 링크를 포트폴리오에 두는 것이 없는 것보다 나쁘다
2. 루트 `requirements.txt` 를 Vercel 과 Streamlit 이 **같이 읽는다.**
   Vercel 이 `uv` + `pyproject.toml` 우선으로 바뀌는 중이라 분리가 확실하지 않다
3. 살리려면 Supabase 를 7일마다 깨워야 한다

### ③ Supabase 를 쓰지 않는다 &nbsp;🔴 **개정됨 → ADR-SC-0010 ④⑤**

팀 노트를 Supabase 에 두려 했으나 7일 pause 가 치명적이다 — 팀원이 일주일 노트를 안 쓰면
잠기고 수동 복구가 필요하다. **팀 노트도 HF dataset 에 둔다**(`stock-coin-trade/team-notes`).
노트 1건 = 파일 1개로 두면 같은 파일을 두 사람이 건드릴 일이 없어 동시 쓰기가 안전하다.
**구성 요소가 하나 줄어든다.**

### ④ 원격을 재구성한다

| 원격 | 대상 | 용도 |
| --- | --- | --- |
| `origin` | `gitlab.com/dev-dongwon05253/stock-coin-trade` | **정본.** 평소 push 대상 |
| `github-est` | `github.com/EST-Bootcamp-Dongwon/stock-coin-trade` | 동시 push 유지 (Flagged 계정) |
| `upstream` | `github.com/edumgt/stock-coin-trade` | fetch 전용 |
| *(없음)* | `github.com/devlee328288/stock-coin-trade` | 🔒 **로컬 원격을 만들지 않는다** |

**🔒 `devlee328288` 원격을 로컬 git 에 추가하지 않는 것이 이 ADR 의 핵심이다.**
GitLab 이 자기 서버에서 devlee328288 토큰으로 push 하므로, 로컬에서 그 계정으로 push 할
**경로 자체가 존재하지 않는다.** 규칙으로 막으면 언젠가 어기지만, 없는 경로는 밟을 수 없다.

**⑤ 모든 git/gh 작업 전에 `gh auth status` 로 활성 계정을 확인한다.**
사용자는 두 계정을 오가므로 세션 중간에도 바뀔 수 있다.
세션 마무리 프롬프트 끝에 **항상 현재 활성 계정을 적는다.**

## 근거

배포 대상을 바꾸는 이유는 "Vercel 이 나빠서"가 아니라 **워크로드가 달라져서**다.
서버리스는 요청-응답에 맞고, 이 도구는 상태를 들고 차트를 그리는 앱이다.

원격 구성은 **금지를 규칙이 아니라 구조로 바꾼 것**이 요점이다.
"조심해서 push 한다"는 언젠가 실패하지만, 원격이 없으면 실패할 수 없다.

## 결과

- ✅ 팀원은 URL 하나만 알면 된다. 설치가 0이다.
- ✅ 공개 저장소는 코드만, 데이터는 HF 비공개 — 쇼케이스와 약관 준수가 양립한다.
- ✅ devlee328288 오push 가 **구조적으로 불가능**하다.
- ⚠️ **push mirror 는 force push 로 동작한다.** GitHub 미러에서 직접 커밋하면 다음 동기화 때
  사라진다. → Issues·Projects·Wiki 를 끄고 README 최상단에 "읽기 전용 미러"를 명시한다.
- ⚠️ **private 앱이 동시 1개**다. 다른 private 앱이 있으면 정리해야 한다.
- ⚠️ 12시간 무트래픽 슬립. 열람 권한자 **누구나** 깨울 수 있으므로 팀 규칙으로 해결한다.
  외부 핑 서비스는 ToS 소지가 있어 쓰지 않는다.
- ⚠️ Vercel 을 되살리려면 ① Supabase restore ② 루트 `requirements.txt` 분리 검증이 선행돼야 한다.

## 후속 (2026-09-11 · M2)

②를 실행했다. **분리는 "검증"이 아니라 하드 제약으로 판명됐다** — Streamlit Cloud 는
의존성 파일을 ① 엔트리포인트 디렉터리 → ② 저장소 루트 순으로 찾고 **처음 만난 하나만**
쓴다(공식 문서). 엔트리포인트가 루트 `streamlit_app.py` 라 두 후보가 한 곳으로 붕괴하고,
배포 UI 에 경로를 지정하는 칸이 없다. 즉 **루트 `requirements.txt` 는 Streamlit 전용이다.**

- Vercel slim 목록과 근거 주석은 `api/requirements.txt` 로 옮겼다 (**보관본**).
  Vercel 문서가 `api/` 안의 의존성 파일을 자동으로 읽는다는 보장을 확인하지 못했으므로,
  되살릴 때는 **루트로 되돌린다.** 절차는 `VERCEL.md` 0장.
- 원격 재구성(④)도 완료했다 — `origin`=GitLab · `github-est`=GitHub ·
  `devlee328288` 로컬 원격 없음.
- 두 배포를 **동시에** 유지하려면 `pyproject.toml` 분리를 따로 검증해야 한다
  (우선순위가 `requirements.txt` 뒤라 여지가 있다). 지금은 하지 않는다.

## 개정 (2026-09-12 · ADR-SC-0010)

②③ 이 무너진 이유는 "판단이 틀려서" 가 아니라 **전제가 바뀌어서**다. 기록해 둔다.

| 여기에 쓴 것 | 무엇이 달랐나 |
|---|---|
| "분리는 검증이 아니라 **하드 제약**" | **Streamlit 쪽만 봤다.** Vercel 프로젝트 설정의 Root Directory + "Include source files outside of the Root Directory" 토글로 `backend/requirements.txt` 를 따로 읽힐 수 있다. 파일을 옮길 필요도 없다 |
| "Supabase 는 **되살려도 7일 뒤 또 멈춘다**" | 이 문장을 쓸 때는 배치 운영 방식이 아직 안 정해져 있었다. 지금은 **매일 `batch.publish` 를 돌린다** — 게시 로그 한 줄이 pause 타이머를 리셋한다 |
| "Vercel 은 무거운 배치가 안 돈다" | **맞다. 지금도 배치를 Vercel 에 올리지 않는다.** 달라진 것은 배치를 로컬에 두기로 이미 정했다는 점이고, 남는 Django 는 읽기 위주 화면이라 서버리스와 맞는다 |

🔒 **교훈 — "안 된다" 를 적을 때는 어느 쪽을 봤는지도 같이 적는다.**
이 문서는 Streamlit 문서만 읽고 "하드 제약" 이라고 썼고, 그 한 단어가 하루 동안
선택지 하나를 지웠다.
