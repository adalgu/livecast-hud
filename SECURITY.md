# Security Policy

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through GitHub instead:
<https://github.com/adalgu/livecast-hud/security/advisories/new>

(Or open the repository's **Security** tab and choose **Report a vulnerability**.) This
uses GitHub's private vulnerability reporting (Security Advisories), so only the
maintainers can see the report.

A useful report includes:

- what an attacker could do and under which conditions,
- steps to reproduce, using the files in `examples/` where possible,
- the LiveCast HUD version (`livecast-hud --version`), macOS version and Python version.

We will acknowledge the report, keep you updated in the advisory, and credit you in the
release notes if you want. This is a small project maintained on a best-effort basis.

Problems in Google Cloud services themselves should go to Google, not to this project.

## Keep secrets out of issues and pull requests

Never paste any of the following into an issue, pull request or discussion, not even
partly redacted:

- service-account keys or other credential files,
- access tokens or the output of `gcloud auth print-access-token`,
- your Google Cloud project ID or account,
- recordings, transcripts or `--log` files from real talks.

If you posted a credential by mistake, revoke or rotate it in the Google Cloud console
right away; deleting the comment is not enough.

## How LiveCast HUD handles data

- The app makes no network calls unless you choose `--stt gcp` or `--mt gcp`.
- It never stores credentials. It uses Google Application Default Credentials, or, if you
  set `LIVECAST_GCP_ACCOUNT`, asks the `gcloud` CLI for a short-lived access token.
- `--log` writes the recognized text to a local file that you choose. Treat that file as
  personal data.
