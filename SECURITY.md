# Security Policy

## Supported versions

Security fixes are intended for the current release.

| Version | Supported |
|---|---|
| 4.0.x | ✅ Supported |
| < 4.0 | ❌ Not supported |

The current release is **4.0**. Versions before 4.0 use OpenAQ API versions that OpenAQ has retired and no longer work, so upgrading to the latest release is required rather than merely recommended.

## Reporting a vulnerability

Please do **not** publish API keys, credentials, home coordinates or exploitable security details in a public GitHub issue.

Use GitHub's private security reporting for this repository where available (**Security → Report a vulnerability**), or contact the maintainer privately.

Provide enough information to reproduce the issue without exposing secrets or personal data.

This is a hobby project maintained in my free time. Reports are reviewed regularly and fixed on a best-effort basis.

## API key and private data

The OpenAQ API key is entered in the Domoticz hardware configuration (field **OpenAQ API-KEY**). Please be aware of the following:

- **The field is not masked.** The key is shown in clear text in the Domoticz hardware form.
- **Debug logging prints the key.** With **Debug** set to `True`, the plugin writes every hardware parameter to the Domoticz log, including the API key, and every Domoticz setting, including your **Location** (latitude and longitude). Keep Debug off in normal operation, and redact these lines before sharing a log. The plugin never writes the key at the normal log level.
- **Where the key goes.** The key is sent only to `api.openaq.org`, in the `X-API-Key` header, over HTTPS.
- **Nothing is written to disk.** The plugin keeps readings in memory and creates no state, cache or log files of its own. Domoticz itself stores the hardware configuration in its database.
- **Rotating a key.** If a key may have been exposed, rotate it on your OpenAQ account page (<https://explore.openaq.org/account>) and enter the new key in Domoticz.

Both the clear-text field and the Debug output are tracked as planned fixes in [`ROADMAP.md`](ROADMAP.md).

Protect the following as sensitive configuration or data:

- The Domoticz hardware configuration, database and backups.
- The plugin directory and host filesystem.
- Logs and diagnostic exports, especially at Debug level.
- Your Domoticz location coordinates.

## Repository hygiene

The following files and directories are runtime or local-development material and must not be committed:

```text
.env
.env.*
*.key
*.pem
*.crt
*.secret
config.local.py
*.log
*.db
*.sqlite
__pycache__/
*.py[cod]
.pytest_cache/
*.swp
*.swo
```

The repository `.gitignore` is configured to prevent accidental publication of these files.

Never commit API keys, credentials, or logs containing them.

If a secret is accidentally committed, deleting the file in a later commit is insufficient. Rotate or revoke the exposed credential first, then remove the secret from the Git history.

## Read-only operation

The plugin only sends `GET` requests to OpenAQ. It does not control any device, has no commands, and opens no listening ports.

## Transport and dependencies

Communication with OpenAQ uses HTTPS. Do not disable TLS certificate verification as a workaround for connectivity problems.

The two one-off startup lookups use Python's default SSL context, with certificate and hostname verification enabled (covered by a test in `tests.py`). The recurring hourly poll uses Domoticz's own HTTPS connection, so its certificate handling is that of your Domoticz build.

The plugin uses only the Python standard library and no third-party packages.
