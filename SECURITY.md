# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue. Use the repository's **Security** tab and choose
**Report a vulnerability**. Include the Shiva version, the steps to reproduce, and what you expected to happen.

If that option is not available, open an issue that only says you have a security report, without technical details,
and a maintainer will arrange a private channel.

Shiva is an early version (before 1.0) and is maintained by one person, so replies may take days.

## What counts

- Plaintext, passphrase or key material that leaks, or a file that is deleted or overwritten when it should not be.
- Cleaning or shredding that claims more than it did.
- Path handling that lets a crafted file or archive write outside the chosen output folder.
- A tampered bundled binary, or a build that does not match its pinned checksums.

Known limits (for example, shredding being best-effort on SSDs) are described under "Honest limits" in the
[README](README.md) and are not vulnerabilities.

## Supported versions

Only the latest release receives fixes.
