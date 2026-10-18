# Contributing to Domoticz-OpenAQ-Plugin

Thank you for your interest in contributing to Domoticz-OpenAQ-Plugin!

Contributions are welcome, including bug reports, testing with other OpenAQ locations and countries, feature suggestions, documentation improvements, and code changes.

The purpose of this guide is to keep contributions safe, reproducible, and easy to review.

## 1. Before You Start

Before opening an issue or submitting a pull request:

1. Read the project's README and existing documentation.
2. Search existing issues and pull requests to avoid duplicates.
3. Check whether the problem is already fixed in the development branch, and whether it is listed in `ROADMAP.md`.
4. Make sure you are using the correct plugin version and a supported Domoticz environment. Plugin versions 4.2 and 4.1 are supported; support for versions before 4.2 will end in a future release (see `SECURITY.md`), so please reproduce a problem on 4.2 where you can.

The plugin relies on the OpenAQ API. API behaviour, available data, rate limits and station coverage may change without notice, and the data available differs from location to location.

## 2. Reporting Bugs

Please use the GitHub Bug Report template.

Include the following information whenever relevant:

- Plugin version.
- Domoticz version and operating system.
- The OpenAQ location ID(s), whether you entered them or the plugin chose them automatically, and how many stations you use.
- Steps to reproduce the issue.
- Expected and actual behaviour.
- Relevant, redacted log output.

Please report whether the issue occurs consistently or intermittently.

**Review your logs before posting them.** Since 4.1 the plugin masks the OpenAQ API key and your location in its own log output (`Parameter 'Mode2'...: '<masked>'`), but versions up to 4.0 write both to the Debug log, and your coordinates also to the normal log when the automatic lookup fails. If you use an older version, redact those lines. Do not attach logs containing API keys, passwords, or your home coordinates.

## 3. Suggesting Features

Use the GitHub Feature Request template.

Explain the problem you are trying to solve, the desired behaviour, and why the feature would be useful.

For features that depend on OpenAQ data, say which pollutants or parameters are involved and whether the OpenAQ API provides them for your location.

## 4. Development Workflow

Contributions should normally target the `development` branch.

Do not submit changes directly to `master` unless the maintainer explicitly requests it.

### Fork and clone

Fork the repository on GitHub, then clone your fork:

```bash
git clone -b development https://github.com/YOUR_USERNAME/Domoticz-OpenAQ-Plugin.git
cd Domoticz-OpenAQ-Plugin
```

Add the upstream repository:

```bash
git remote add upstream https://github.com/janreimen/Domoticz-OpenAQ-Plugin.git
git fetch upstream
```

Create a feature branch:

```bash
git switch -c feature/short-description
```

For a bug fix, a name such as `fix/heartbeat-after-failed-start` is also suitable.

### Keep your branch up to date

```bash
git fetch upstream
git rebase upstream/development
```

Resolve any conflicts before continuing.

### Commit your changes

Use clear commit messages that describe the change:

```bash
git add .
git commit -m "Guard heartbeat when startup failed"
git push -u origin feature/short-description
```

Open a pull request against the upstream `development` branch.

Replace the example branch names and commit message with ones that describe your actual changes.

## 5. Coding Guidelines

- Follow the existing Python style and project structure.
- Keep changes focused on one issue or feature wherever practical.
- Avoid unrelated refactoring in bug-fix pull requests.
- Use only the Python standard library. Avoid introducing new dependencies unless they are justified and documented.
- Handle API errors, timeouts, and unexpected responses safely.
- Respect OpenAQ's rate limits (60 requests per minute and 2,000 per hour on the free tier) and keep the recurring poll to a single request per interval.
- Do not log the API key or other credentials, and do not log the Domoticz location or any request path that contains it, at any log level. New log output goes through the existing masking (`mask_value`, the path and key scrubbing); `tests.py` fails if the key or the location reaches any log line, so add a test for new log output that touches request data.
- Log messages quoted in `README.md` and `DEPLOY.md` must exist in `plugin.py`; a test checks this, so update the docs when you change a message.
- Preserve the Domoticz device unit numbers and their meaning, so existing devices and their history survive an upgrade. Each station owns a block of ten units: the first station uses units 1–10 (as in 4.0 and 4.1), further stations 11–20, 21–30 and so on, up to 20 stations. Within a block the order of the pollutants is fixed. The `OAQ<id>-<n>` DeviceID tag and the text `OpenAQ Location ID: <id>` of the Info device are how the plugin recognises which station owns a block, and the Info text is also how devices created by 4.0 and 4.1 are recognised, so neither format may change without a migration and a test that covers it.
- Ensure that a failure in one operation does not disrupt unrelated devices or the rest of the plugin. In particular, one failing station must never stop the others, and a station must never receive another station's readings.

## 6. Testing

Before submitting a pull request:

- Run the test suite and check that all tests pass.
- Add or update tests for the behaviour you changed.
- Review the logs for new errors or unexpected behaviour.
- Test the affected functionality in Domoticz where possible.
- Verify that existing devices continue to update correctly.
- Document any tests you could not perform.

The test suite needs neither Domoticz nor network access:

```bash
python3 tests.py
python3 -m py_compile plugin.py tests.py
```

Run both commands from the repository root. The tests replace the Domoticz plugin API with an in-memory fake, so they are not a substitute for running the plugin in Domoticz against the real OpenAQ API.

If you cannot test against a real Domoticz installation, state that clearly in your pull request.

## 7. Location and Data Compatibility Testing

Testing with additional OpenAQ locations is particularly valuable, because the pollutants and units available differ from station to station and country to country.

When reporting compatibility, include the OpenAQ location ID (one per station), the country, the plugin version, and which of the pollutants (BC, CO, NO<sub>2</sub>, O<sub>3</sub>, PM<sub>10</sub>, PM<sub>2.5</sub>, SO<sub>2</sub>) the location reports, including the unit OpenAQ gives for each.

Distinguish between:

- Confirmed working.
- Partially working.
- Not reported by this location.
- Not tested.

A pollutant that a location does not measure is not a plugin defect.

## 8. Security and Privacy

Never commit or publish:

- API keys or access tokens.
- Passwords, cookies, or authentication headers.
- `.env` files containing secrets.
- Your home coordinates or other personal data.
- Unredacted diagnostic logs.

Use placeholders in examples, such as `YOUR_API_KEY`.

If you discover a security vulnerability, do not publish exploitable details or credentials in a public issue. Contact the maintainer privately; see `SECURITY.md`.

## 9. Pull Request Requirements

Every pull request should:

- Target the `development` branch unless otherwise agreed.
- Explain the problem and the proposed solution.
- Reference the relevant issue, if one exists.
- Describe the tests performed and their results.
- Identify any limitations or untested behaviour.
- Include documentation updates when necessary.
- Avoid unrelated files, generated artifacts, and secrets.

The maintainer may request changes before merging.

## 10. Releases

Release and stable-branch changes are managed by the project maintainer.

Do not independently publish a release, change release tags, or merge into `master` without coordination.

Keep development changes focused and ensure that the version shown in `plugin.py`, `README.md`, `CHANGELOG.md`, `SECURITY.md` and `ROADMAP.md` stays consistent when requested by the maintainer. `tests.py` checks this, and also checks that the support policy (4.2 and 4.1 supported, versions before 4.2 ending in a future release) is stated in `README.md`, `SECURITY.md` and `ROADMAP.md`. Releases are tagged with the plain version number, for example `4.2`.

## 11. Questions and Support

For reproducible bugs and feature requests, use the repository's GitHub issue templates.

For general Domoticz usage questions, consult the Domoticz community resources and existing project documentation.

Thank you for helping improve Domoticz-OpenAQ-Plugin!
