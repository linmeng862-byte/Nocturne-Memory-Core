# Environment variables

## Core

| Variable | Purpose | Default |
|---|---|---|
| `OMBRE_TRANSPORT` | `stdio`, `streamable-http`, or `sse` | `stdio` |
| `OMBRE_PORT` | HTTP/Dashboard port | `8000` |
| `OMBRE_BUCKETS_DIR` | private Markdown and continuity data | `./buckets` |
| `OMBRE_API_KEY` | optional OpenAI-compatible model key | empty |
| `OMBRE_BASE_URL` | optional common API base URL | config file |
| `OMBRE_MODEL` | chat/compression model | config file |
| `OMBRE_API_PASSWORD` | Dashboard and administrative API password | first-run setup |

`OMBRE_DASHBOARD_PASSWORD` remains a compatibility alias for the password.

## Identity

| Variable | Purpose | Default |
|---|---|---|
| `OMBRE_AGENT_NAME` | local agent display label | `Agent` |
| `OMBRE_HUMAN_NAME` | local human display label | `Human` |
| `OMBRE_AGENT_PERSONA` | optional local first-person orientation | neutral text |

Identity configuration belongs to each installation and is never required to
use the continuity mechanisms.

## Optional integrations

| Variable | Purpose |
|---|---|
| `OMBRE_HOOK_URL` | fire-and-forget event webhook |
| `OMBRE_HOOK_SKIP` | disable event webhooks |
| `OMBRE_NOW_PLAYING_COMMAND` | local executable for optional now-playing data |
| `OMBRE_MEMORY_ANALYZER` | `dp` or `cli` memory-analysis path |
| `OMBRE_QUIET_HOURS` | quiet-hour range used by absence/longing logic |
| `OMBRE_QUIET_TZ` | timezone for quiet hours | `UTC` |

Keep values in an untracked `.env` or secret manager.

## OMBRE_API_TOKEN

A secret for clients that are not people — her companion, the hooks, anything
calling the HTTP API without a browser session.
给「不是人」的客户端用的秘密 —— 她的伴、那些钩子、任何不带浏览器 session 调 HTTP API 的东西。

Present it as either header:

    Authorization: Bearer <token>
    X-Nocturne-Token: <token>

Deliberately separate from the login password:
刻意跟登录密码分开：

- **HTTP headers are ASCII.** A non-ASCII password (Chinese, for instance)
  cannot be sent in a header at all — the client refuses to encode it before
  the request even leaves. Reusing the password as the machine credential
  would silently forbid choosing a Chinese one.
  **HTTP header 是 ASCII 的。** 非 ASCII 的密码（比如中文）根本没法放进 header ——
  请求还没发出去，客户端就拒绝编码了。把密码复用成机器凭据，等于悄悄禁止用中文密码。

- A machine credential and a person's password want different lifetimes and
  different blast radius. Rotating one should not mean retyping the other.
  机器凭据和人的密码，该有不同的寿命和不同的爆炸半径。换掉一个，不该意味着重敲另一个。

An ASCII login password still works as a bearer, so an existing setup that
relies on that keeps working.
ASCII 的登录密码仍然可以当 bearer 用，所以已经这么跑的部署不会断。
