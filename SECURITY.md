# Security Policy

Shreyzen ships load, performance, and **security** testing tooling, so we take
the security of the project itself seriously. Thank you for helping keep it and
its users safe.

## Supported Versions

Shreyzen is under active development. Security fixes are applied to the latest
`main` branch. Please make sure you're on the most recent version before
reporting.

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

Instead, report them privately using **GitHub's private vulnerability reporting**:

1. Go to the [Security tab](https://github.com/ShreyasKereRavindranath/playwrightAI/security) of the repository.
2. Click **"Report a vulnerability"** to open a private advisory.

If you cannot use that channel, contact the maintainer directly via their
[GitHub profile](https://github.com/ShreyasKereRavindranath)
(**[INSERT CONTACT METHOD — e.g. email]**).

Please include as much of the following as you can:

- The type of issue (e.g. injection, credential exposure, SSRF, path traversal).
- Affected file(s), component, or endpoint (Studio, mock API, load runner, LLM layer, etc.).
- Step-by-step instructions to reproduce.
- Proof-of-concept or exploit code, if available.
- The potential impact, including how an attacker might exploit it.

## What to Expect

- We'll acknowledge your report as soon as we reasonably can.
- We'll investigate, keep you updated on progress, and let you know when a fix ships.
- We'll credit you for the discovery unless you prefer to remain anonymous.

## Scope Notes

- The bundled **mock API**, sample data, and security *test scenarios* are
  intentionally exercisable for testing purposes; findings there are only
  in scope if they affect users of the framework beyond the intended test
  sandbox.
- **Never** include real credentials, secrets, or production data in reports,
  issues, or PRs. Configure secrets via `config/.env` (git-ignored), never in
  committed code.

## Responsible Disclosure

Please give us a reasonable amount of time to address an issue before any public
disclosure. We're grateful for coordinated, good-faith reporting.
