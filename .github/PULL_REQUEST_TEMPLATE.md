# Pull Request

## Summary

<!-- Briefly explain what this pull request changes. -->

## Related issue

<!-- Link the issue, for example: Closes #12 -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Location / data compatibility improvement
- [ ] Refactoring or code quality improvement
- [ ] Documentation update
- [ ] Other

## Changes made

<!-- Describe the important changes and their impact. -->

-
-
-

## Testing performed

- [ ] `python3 tests.py` passes.
- [ ] Python syntax check completed (`python3 -m py_compile plugin.py tests.py`).
- [ ] Tests added or updated for the changed behaviour.
- [ ] Plugin startup tested in Domoticz.
- [ ] Relevant functionality tested.
- [ ] Existing device creation and updates checked, where applicable.
- [ ] Error handling and logging reviewed.
- [ ] Documentation updated, where necessary.

### Test environment

- **Plugin version / branch:**
- **Domoticz version:**
- **Operating system:**
- **OpenAQ location ID(s) / country:** <!-- If applicable -->
- **Number of stations; entered or auto-detected:** <!-- If applicable -->

### Test results

<!-- Describe what you tested and the results. If testing was not possible, explain why. -->

## Compatibility and limitations

<!-- Describe any location-specific behaviour, API limitations, migration considerations, or untested functionality. -->

## Security and privacy

- [ ] No API keys, access tokens, passwords, or other secrets are included.
- [ ] No home coordinates or other personal data are included.
- [ ] Logs, screenshots, and test data have been reviewed and redacted.
- [ ] The change does not log the API key or the Domoticz location, and `tests.py` still checks this for any new log output.

## Checklist

- [ ] This pull request targets the `development` branch.
- [ ] I reviewed the existing code and followed the project conventions.
- [ ] My changes are focused and do not contain unrelated modifications.
- [ ] Device unit numbers (ten per station, the first station on units 1–10), the `OAQ<id>-<n>` DeviceID tags and the Info device text are unchanged, or the change is explained above with a migration.
- [ ] I updated relevant documentation.
- [ ] I have not included generated files or temporary development artifacts unnecessarily.
- [ ] I have documented any tests I could not perform.

## Additional notes

<!-- Anything else the maintainer should know before reviewing this pull request? -->
