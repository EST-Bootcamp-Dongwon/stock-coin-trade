"""코멘트에 붙는 **링크** — 받는 규칙과 저장할 모양.

## 🔴 이것은 SSRF 가드가 아니다 — 서버가 링크를 부르지 않기 때문이다

SSRF 는 서버가 사용자가 준 주소로 **요청을 보낼 때** 생긴다. 이 앱은 링크를 저장하고
`<a>` 로 그릴 뿐 열어 보지 않는다 — 제목 미리보기도 없다(ADR-SC-0012 ①). 누르면
**그 팀원의 브라우저**가 연다. 그래서 SSRF 표면이 0 이고, 그렇게 두기로 했다.

미리보기를 긁지 않는 이유가 하나 더 있다. 팀원이 붙이는 링크의 상당수는 네이버
뉴스일 텐데, 네이버 약관이 봇 자동 수집을 금지한다(V7). 서버가 긁는 순간 그 봇이 된다.

🔴 **나중에 서버가 링크를 부르게 되면 이 검사로는 모자란다.** 호스트 이름이 공인
   도메인이어도 DNS 가 사설 IP 를 돌려줄 수 있다(DNS rebinding). 그때는 **연결 직전에
   해석된 IP** 를 검사하고 그 IP 로 붙어야 한다. 함수 이름이 `is_public_url` 이 아닌
   이유다 — 그 이름은 이 모듈이 지키지 못하는 약속을 한다.

## 그러면 호스트 규칙은 무엇을 막나

1. **누르는 사람의 내부망으로 가는 링크** — `https://192.168.0.1/…` 는 그 팀원의
   공유기를 향한다. 공개 원장(ADR-SC-0011 ⑥)에 적힌 링크가 그 길을 열면 안 된다.
2. **보이는 곳과 가는 곳이 다른 링크** — `https://naver.com@evil.example/` 은
   `naver.com` 으로 읽히지만 `evil.example` 로 간다. 국제화 도메인은 **퓨니코드로 굳혀**
   생김새가 같은 다른 글자(동형문자)가 드러나게 한다.
3. **숫자 호스트** — 브라우저는 `https://2130706433/` · `https://0x7f.1/` 을 IPv4 로
   읽는다(WHATWG URL). 마지막 라벨이 글자가 아니면 받지 않는다.

## 🔒 굳힌 모양이 곧 저장 모양이다 — 멱등이어야 한다

`normalize_link(normalize_link(x)) == normalize_link(x)`. `fold` 가 원장에서 읽은 링크를
**다시 굳혀 보고 같을 때만** 그린다. passcode 를 가진 사람이 RPC 로 앱을 거치지 않고
쓴 링크를 거기서 거른다.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Iterable
from urllib.parse import quote, urlsplit

__all__ = [
    "LinkError",
    "MAX_LINKS",
    "MAX_LINK_LEN",
    "display_link",
    "normalize_link",
    "normalize_links",
]


class LinkError(ValueError):
    """링크를 받을 수 없다. 🔒 메시지가 **무엇을 고치면 되는지** 말한다."""


#: 코멘트 하나에 붙일 수 있는 링크 수와 길이.
#: 🔒 payload 상한(16384바이트) 안에 **한글 본문 4000자 + 링크 3개×500자**가 든다
#:    (13,592바이트 · 2026-09-14 계산). 늘리려면 그 계산을 다시 한다.
MAX_LINKS = 3
MAX_LINK_LEN = 500

#: DNS 라벨 — 영문 소문자·숫자·하이픈이고 양끝은 하이픈이 아니다 (퓨니코드 포함).
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
#: 🔴 마지막 라벨(최상위 도메인)은 **글자**여야 한다. 브라우저는 마지막 라벨이 숫자이거나
#:    `0x` 로 시작하면 호스트 전체를 IPv4 로 읽는다 — `https://0x7f.1/` 은 127.0.0.1 이다.
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
#: 공인 인터넷에 없는 이름 — 누르는 사람의 **내부망**을 가리킨다.
#: 🔒 IANA 특수용도(`localhost`·`local`·`test`·`example`·`invalid`·`onion`·`home.arpa`)와
#:    ICANN 이 위임하지 않기로 한 사설용(`internal`·`lan`·`home`·`corp`)이다.
_PRIVATE_SUFFIXES = ("localhost", "local", "internal", "lan", "home", "corp",
                     "test", "example", "invalid", "onion", "arpa")
#: 경로·질의·조각에서 **그대로 두는** 글자. `%` 가 들어 있어 이미 인코딩된 것을 다시
#: 인코딩하지 않는다 — 그래야 굳히기가 멱등이다.
_SAFE_PATH = "/%:@!$&'()*+,;=-._~"
_SAFE_QUERY = _SAFE_PATH + "?"
_PREVIEW = 60


def _preview(text: str) -> str:
    """오류 문장에 넣을 앞부분. 🔒 긴 주소를 통째로 되돌려 주지 않는다.

    🔒 짝 없는 서로게이트는 `?` 로 바꾼다 — 그대로 두면 오류 문장을 화면에 보낼 때 인코딩이 깨진다.
    """
    cut = text if len(text) <= _PREVIEW else text[:_PREVIEW] + "…"
    return cut.encode("utf-8", "replace").decode("utf-8")


def normalize_link(raw: str) -> str:
    """링크 하나를 **받을지 판단하고 저장할 모양으로 굳힌다.** 못 받으면 `LinkError`.

    🔒 굳힌 결과를 다시 넣으면 **같은 문자열**이 나온다 (머리주석 — `fold` 가 기대는 성질).
    """
    if not isinstance(raw, str):
        raise LinkError(f"링크는 문자열이어야 한다: {type(raw).__name__}")
    text = raw.strip()
    if not text:
        raise LinkError("빈 링크다")
    shown = _preview(text)
    # 🔴 브라우저는 주소 속 탭·줄바꿈을 **지우고** 읽는다. 여기서 받아 주면 저장한 것과
    #    실제로 가는 곳이 달라진다.
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        raise LinkError(
            f"링크 안에 공백이나 보이지 않는 글자가 있다 — 한 줄에 링크 하나만 붙인다: {shown}")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise LinkError(f"링크 모양이 아니다: {shown}") from exc

    scheme = parts.scheme.lower()
    if scheme == "http":
        raise LinkError(f"http 링크는 받지 않는다 — 주소 앞을 https 로 바꿔 붙인다: {shown}")
    if scheme != "https":
        raise LinkError(f"https 링크만 받는다: {shown}")
    if "@" in parts.netloc:
        raise LinkError(
            f"주소에 @ 가 있다 — 보이는 곳과 실제로 가는 곳이 다를 수 있어 받지 않는다: {shown}")
    if port not in (None, 443):
        raise LinkError(f"443 이 아닌 포트는 받지 않는다: {shown}")

    host = _host(parts.hostname or "", shown)
    try:
        link = f"https://{host}{quote(parts.path, safe=_SAFE_PATH) or '/'}"
        if parts.query:
            link += "?" + quote(parts.query, safe=_SAFE_QUERY)
        if parts.fragment:
            link += "#" + quote(parts.fragment, safe=_SAFE_QUERY)
    except UnicodeError as exc:
        # 🔴 짝 없는 서로게이트는 UTF-8 로 인코딩되지 않는다. `LinkError` 로 바꾸지 않으면
        #    `fold` 가 못 잡아 원장 한 줄이 조 화면 전체를 죽인다 (2026-09-14 리뷰)
        raise LinkError(f"주소에 읽을 수 없는 글자가 있다: {shown}") from exc
    if len(link) > MAX_LINK_LEN:
        raise LinkError(
            f"링크가 너무 길다 ({len(link)}자 > {MAX_LINK_LEN}자). "
            f"추적용 꼬리(?utm_…)를 지우고 붙인다")
    return link


def _host(hostname: str, shown: str) -> str:
    """호스트를 판정하고 퓨니코드 소문자로 굳힌다. `urlsplit` 이 이미 소문자로 줬다."""
    host = hostname[:-1] if hostname.endswith(".") else hostname   # 끝의 점은 같은 이름이다
    if not host:
        raise LinkError(f"주소에 호스트가 없다: {shown}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise LinkError(f"IP 주소 링크는 받지 않는다 — 누르는 사람의 내부망을 가리킬 수 있다: {shown}")
    if not host.isascii():
        try:
            ascii_host = host.encode("idna").decode("ascii")
            back = ascii_host.encode("ascii").decode("idna")
        except UnicodeError as exc:
            raise LinkError(f"도메인 이름을 읽을 수 없다: {shown}") from exc
        # 🔴 파이썬의 IDNA(2003)는 글자를 **다른 이름으로 바꿔** 준다 — `faß.de` → `fass.de`,
        #    한 점 리더(U+2024) → `.`. 브라우저는 다른 곳으로 간다. 되돌렸을 때 같은 이름일
        #    때만 받는다 (2026-09-14 리뷰)
        if back != host:
            raise LinkError(
                f"국제화 도메인이 변환하면 다른 이름이 된다 — 주소창에 보이는 그대로 붙인다: {shown}")
        host = ascii_host
    labels = host.split(".")
    if len(labels) < 2 or not all(_LABEL_RE.match(label) for label in labels):
        raise LinkError(f"공개 도메인 이름이 아니다: {shown}")
    if not _TLD_RE.match(labels[-1]):
        raise LinkError(f"숫자로 끝나는 주소는 받지 않는다 — 브라우저가 IP 로 읽는다: {shown}")
    if any(host == suffix or host.endswith("." + suffix) for suffix in _PRIVATE_SUFFIXES):
        raise LinkError(f"내부망 이름은 받지 않는다: {shown}")
    return host


def normalize_links(raws: Iterable[str]) -> tuple[str, ...]:
    """여러 줄 입력 → 굳힌 링크들. 빈 줄은 건너뛰고 **같은 링크는 하나로** 둔다.

    🔒 문자열 하나를 통째로 넘기면 글자마다 링크로 읽게 된다 — 목록으로만 받는다.
    """
    if isinstance(raws, str):
        raise LinkError("링크는 목록으로 준다 (한 줄에 하나)")
    kept: list[str] = []
    for raw in raws:
        if isinstance(raw, str) and not raw.strip():
            continue
        link = normalize_link(raw)
        if link not in kept:
            kept.append(link)
    if len(kept) > MAX_LINKS:
        raise LinkError(f"링크는 {MAX_LINKS}개까지다 ({len(kept)}개). 가장 직접적인 근거만 남긴다")
    return tuple(kept)


def display_link(link: str, *, limit: int = 60) -> str:
    """화면에 보일 글자 — `https://` 를 떼고, 길면 **경로만** 자른다.

    🔒 **사용자가 링크에 이름을 붙이지 못한다.** `[공시 원문](https://…)` 처럼 글자를
       고르게 하면 보이는 것과 가는 곳이 달라진다. 주소 자체를 보여주고, 국제화 도메인도
       퓨니코드 그대로 둔다 — 생김새가 같은 다른 글자가 드러나야 한다.
    🔴 **호스트는 자르지 않는다.** `n.news.naver.com.….attacker.com` 을 60자에서 자르면
       `naver.com` 만 보이고 실제 도메인이 가려진다 (2026-09-14 리뷰).
    """
    text = link.removeprefix("https://")
    if len(text) <= limit:
        return text
    host, _, rest = text.partition("/")
    room = max(limit - len(host) - 2, 0)      # "/" 와 "…" 자리
    return f"{host}/{rest[:room]}…"
