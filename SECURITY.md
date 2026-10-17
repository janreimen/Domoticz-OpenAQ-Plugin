# Security Policy

## Supported versions

Security fixes are intended for the current release.

| Version | Supported |
|---|---|
| 4.1.x | ✅ Supported |
| 4.0.x | ❌ Not supported: shows the API key in clear text in the hardware form, and writes the key and your location to the log with Debug on (and your coordinates to the log on a failed lookup) |
| < 4.0 | ❌ Not supported: these versions use OpenAQ API versions that OpenAQ has retired |

The current release is **4.1**. Upgrading from 4.0 is safe: devices and history are kept.

## Reporting a vulnerability

Please do **not** publish API keys, credentials, home coordinates or exploitable security details in a public GitHub issue.

Use GitHub's private security reporting for this repository where available (**Security → Report a vulnerability**), or contact the maintainer privately.

Provide enough information to reproduce the issue without exposing secrets or personal data.

This is a hobby project maintained in my free time. Reports are reviewed regularly and fixed on a best-effort basis.

## API key and private data

The OpenAQ API key is entered in the Domoticz hardware configuration (field **OpenAQ API-KEY**).

- **The field is masked** in the Domoticz hardware form (since 4.1). Domoticz stores the value in its database, so protect that database and its backups.
- **The log never contains the key or your location** (since 4.1), even with Debug on:
  - The Debug dump of the hardware parameters and Domoticz settings shows a value only for an allow-list of harmless names (radius, location ID, Debug setting, Domoticz's own folder and version entries). Everything else, including the API key and your Domoticz location, is shown as `<masked>` (or `<empty>`), and names that are not on the list are masked by default.
  - Request paths are logged without their query string, because the automatic lookup carries your coordinates there.
  - The API key is scrubbed from logged response text and error messages.
  - These guarantees are covered by tests in `tests.py`. The plugin does not control Domoticz's own log lines.
- **Older versions leak.** Versions up to 4.0 write the key and your location to the Debug log, and your coordinates also to the normal log when the automatic lookup fails. **If you ever shared a log from such a version, rotate your key.**
- **Where the key goes.** The key is sent only to `api.openaq.org`, in the `X-API-Key` header, over HTTPS.
- **Nothing is written to disk.** The plugin keeps readings in memory and creates no state, cache or log files of its own.
- **Your location** is sent to OpenAQ only in the automatic lookup request (rounded to four decimal places). With an OpenAQ Location ID entered, the plugin does not read your Domoticz location at all.
- **Rotating a key.** If a key may have been exposed, rotate it on your OpenAQ account page (<https://explore.openaq.org/account>) and enter the new key in Domoticz.

Protect the following as sensitive configuration or data:

- The Domoticz hardware configuration, database and backups.
- The plugin directory and host filesystem.
- Logs and diagnostic exports. Even though the plugin masks its own output, review a log before sharing it.
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
