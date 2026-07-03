# Security Policy

HydraMind is in alpha (`0.1.x`). Security fixes land on the latest `0.1.x` only.

| Version | Supported |
|---|---|
| `0.1.x` (alpha) | ✅ latest patch |
| `< 0.1` | ❌ |

## Reporting a vulnerability

Please report security issues **privately**, not via public issues or pull
requests.

- Preferred: open a [GitHub private security advisory](https://github.com/ChristopheZhao/HydraMind/security/advisories/new).
- Alternatively: email the maintainer at `398453241@qq.com` with `HydraMind
  security` in the subject.

Include the affected version/commit, a description, and a minimal reproduction
if possible. You can expect an acknowledgement within a few days. Please give a
reasonable disclosure window before any public discussion.

## Scope notes

HydraMind orchestrates calls to external LLM/tool backends through the
`ModelProvider` boundary (vendor SDKs are confined to the `harness/` package);
it does not ship model weights or a hosted service.
Treat provider API keys and any tool credentials as secrets supplied via the
environment — they are never persisted to `RuntimeSession` state or trace
artifacts (observability records are redacted previews only).
