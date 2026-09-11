# ADR-SC-0008 — 배포를 Streamlit Community Cloud 로 옮기고 원격을 재구성한다

- **상태**: 채택
- **날짜**: 2026-09-11
- **관련**: [ADR-SC-0005](0005-산출물-재지정과-이력-재시작.md) · [ADR-SC-0006](0006-krx-데이터-제3자-제공-금지.md)

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

### ② Vercel 연동을 해제한다. 코드는 보존한다

`api/index.py` · `vercel.json` · `backend/` 를 지우지 않는다.
Vercel 에서 **Git 연동만 끊는다.** 되살리고 싶으면 연동을 다시 걸면 된다.

근거 세 가지:
1. 깨진 링크를 포트폴리오에 두는 것이 없는 것보다 나쁘다
2. 루트 `requirements.txt` 를 Vercel 과 Streamlit 이 **같이 읽는다.**
   Vercel 이 `uv` + `pyproject.toml` 우선으로 바뀌는 중이라 분리가 확실하지 않다
3. 살리려면 Supabase 를 7일마다 깨워야 한다

### ③ Supabase 를 쓰지 않는다

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
