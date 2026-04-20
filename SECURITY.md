# Security Policy

Dory handles agent memory and can index sensitive local markdown. Treat security and privacy reports seriously.

## Supported Versions

Until Dory has stable versioned releases, security fixes target `main`.

## Reporting A Vulnerability

Use GitHub private vulnerability reporting if it is enabled for this repository. If it is not available, open a minimal public issue asking for a private security contact path. Do not include exploit details, tokens, private corpus snippets, or personal data in a public issue.

Helpful report details:

- Affected surface: CLI, HTTP daemon, MCP server, browser wiki, Hermes provider, OpenClaw plugin, Docker image, or scripts.
- Impact and prerequisites.
- Minimal reproduction using synthetic data.
- Version, commit SHA, deployment shape, and relevant configuration with secrets redacted.

## Public-Safety Scope

Please report:

- Authentication or authorization bypass.
- Token leakage, secret handling bugs, or unsafe defaults.
- Private corpus, session, or profile data exposure.
- Unsafe path handling, archive extraction, or file writes.
- Remote code execution, SSRF, command injection, or dependency confusion.

Please do not submit real private corpora or personal memories as proof. Use synthetic reproductions.
