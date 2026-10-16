# Deployment Guide

## Domoticz OpenAQ Plugin

This document describes how to install, configure, update, and remove the **OpenAQ 4** plugin for Domoticz.

The plugin reads the latest air-quality measurements for one OpenAQ location and exposes them as Domoticz devices. It is read-only: it only sends `GET` requests to OpenAQ.

---

## 1. Requirements

Before installing the plugin, make sure the following requirements are met:

* A working Domoticz installation with Python plugin support
* Python 3 (the interpreter used by Domoticz)
* Internet connectivity from the Domoticz host to `api.openaq.org` on port 443
* An OpenAQ API key (see section 2)
* Permission to write to the Domoticz plugins directory

The plugin uses only the Python standard library. There is nothing to install with `pip`.

---

## 2. Get an OpenAQ API key

1. Create a free account at <https://explore.openaq.org/register>.
2. Open <https://explore.openaq.org/account> to view your API key. You can also rotate it there.

Treat the key like a password. Requests without a valid key are denied by OpenAQ.

The free tier allows 60 requests per minute and 2,000 per hour. The plugin polls about once per hour, plus one short lookup each time it starts (two if it has to find a location automatically).

---

## 3. Installation

### 3.1 Locate the Domoticz plugins directory

The plugin must be installed inside the Domoticz `plugins` directory.

Typical locations include:

```text
/opt/domoticz/plugins
```

or, depending on the installation:

```text
/home/pi/domoticz/plugins
```

For a Docker-based Domoticz installation, use the directory that is mapped into the container's Domoticz plugins directory (see section 14).

### 3.2 Clone the repository

```bash
cd /opt/domoticz/plugins
git clone https://github.com/janreimen/Domoticz-OpenAQ-Plugin.git
```

The resulting directory should look similar to:

```text
plugins/
└── Domoticz-OpenAQ-Plugin/
    ├── plugin.py
    ├── xfr_openaq2.zip
    ├── README.md
    ├── DEPLOY.md
    ├── SECURITY.md
    ├── LICENSE
    └── ...
```

`plugin.py` and `xfr_openaq2.zip` (the device icons) must both be present.

### 3.3 File permissions

The Domoticz process must be able to read the plugin files:

```bash
sudo chown -R domoticz:domoticz /opt/domoticz/plugins/Domoticz-OpenAQ-Plugin
ls -la /opt/domoticz/plugins/Domoticz-OpenAQ-Plugin
```

Adjust the user and group if Domoticz runs under a different account.

### 3.4 Syntax check (optional)

```bash
python3 -m py_compile /opt/domoticz/plugins/Domoticz-OpenAQ-Plugin/plugin.py
```

A successful command produces no output.

---

## 4. Set your location in Domoticz

Open **Setup → Settings → System → Location** and make sure your latitude and longitude are set.

The plugin reads these coordinates at startup. They are used for the automatic location lookup (section 5), but a start with an empty location currently fails even if you enter a Location ID. See `ROADMAP.md`.

---

## 5. Choose an OpenAQ location

Check <https://explore.openaq.org> to see which locations exist near you, and which pollutants they report.

You have two options:

* **Enter a Location ID** (recommended). The plugin reads exactly that location, for example `9645` (Beidweiler, LU).
* **Leave the field blank.** The plugin asks OpenAQ for one location within the radius of your Domoticz coordinates, once at startup, and logs it:

```text
Auto-selected nearest OpenAQ location: <name> (id <id>)
```

OpenAQ's API documentation does not state that results are ordered by distance, so the automatic choice is not guaranteed to be the closest location. After the first start, copy the logged ID into the **OpenAQ Location ID** field to make the choice permanent.

The radius is in kilometres, defaults to `10`, and is only used for the automatic lookup. OpenAQ documents a maximum of 25 km.

---

## 6. Configure the plugin

Start or restart Domoticz after installing the plugin, then open:

```text
Setup → Hardware
```

Create a new hardware instance and select the type:

```text
OpenAQ 4
```

| Field | Value |
|---|---|
| Radius (km, used only if Location ID is blank) | `10`, or up to `25` |
| OpenAQ API-KEY | Your OpenAQ API key |
| OpenAQ Location ID (blank = auto-detect nearest) | A numeric location ID, or blank |
| Debug | `False` for normal use |

**Do not put the API key into `plugin.py`.** Enter it only in the Domoticz hardware form.

Keep **Debug** set to `False` unless you are diagnosing a problem. With Debug on, the plugin writes your API key and your Domoticz location to the log (see section 11).

Click **Add**.

---

## 7. Restart Domoticz

After changing the plugin files, restart Domoticz.

For a systemd installation:

```bash
sudo systemctl restart domoticz
sudo systemctl status domoticz
```

For Docker:

```bash
docker restart domoticz
```

Replace `domoticz` with the actual container name if necessary.

---

## 8. Verify the installation

After restarting Domoticz:

1. Open **Setup → Hardware** and verify that the **OpenAQ 4** instance is present and enabled.
2. Open **Setup → Devices** and verify that ten devices exist: BC, CO, NO<sub>2</sub>, O<sub>3</sub>, PM<sub>10</sub>, PM<sub>2.5</sub>, SO<sub>2</sub>, Info, Pollutants and Air Quality Index.
3. Check the **Info** device. It should read `OpenAQ Location ID: <id>` and `Sensors reporting: <n>/<m>`.
4. Check the Domoticz log for plugin errors.

At the normal log level a successful start is quiet; the only message the plugin itself writes is the auto-selection line described in section 5. The devices are created at startup and receive their values after the first successful poll.

A pollutant device only receives a value if the location reports that pollutant. Devices for pollutants your location does not measure stay empty. This is normal.

---

## 9. Updating the plugin

Before updating, read the release notes in `CHANGELOG.md` for the target version.

### Recommended Git update

```bash
cd /opt/domoticz/plugins/Domoticz-OpenAQ-Plugin
git status
```

If the working tree is clean, fetch the latest tags:

```bash
git fetch --tags
git tag
```

Check out the desired release:

```bash
git checkout <VERSION>
```

For example:

```bash
git checkout v4.0
```

Restart Domoticz:

```bash
sudo systemctl restart domoticz
```

### Updating to the latest repository state

```bash
cd /opt/domoticz/plugins/Domoticz-OpenAQ-Plugin
git fetch origin
git pull --ff-only
sudo systemctl restart domoticz
```

Using `--ff-only` prevents Git from silently creating an unexpected merge commit on the deployment host.

### Important

Do not overwrite a working installation blindly with files from another release. Keep the deployed version identifiable so that the installation can be rolled back.

---

## 10. Release deployment and rollback

Production installations should use a tagged release rather than an arbitrary development commit:

```bash
git fetch --tags
git checkout <VERSION>
git describe --tags --always
git status
```

The deployment should report the intended release.

### Branch workflow

* `master` is the stable branch for production installations.
* `development` is for testing new changes. Contributions target it (see `CONTRIBUTING.md`).
* After a change has been tested, the maintainer merges `development` into `master` and tags the release.

### Rollback

If a new release causes problems, return to the previously working release:

```bash
cd /opt/domoticz/plugins/Domoticz-OpenAQ-Plugin
git tag
git checkout <PREVIOUS_VERSION>
sudo systemctl restart domoticz
```

Then verify the Domoticz log.

---

## 11. Logging and diagnostics

When diagnosing problems, collect the relevant Domoticz log entries.

**With Debug enabled, the plugin writes your OpenAQ API key and your Domoticz location to the log.** Look for these lines and replace the values before sharing the log:

```text
Parameter 'Mode2'...: '<REDACTED>'
Setting 'Location'...: '<REDACTED>'
```

`Mode2` is the API key. Also avoid posting:

* Your API key
* Your Domoticz location coordinates
* Any other credentials or personal data

Turn Debug off again when you are done. If the key may have been exposed, rotate it on your OpenAQ account page and enter the new key in the Domoticz hardware form.

---

## 12. Troubleshooting

### Plugin does not appear in Domoticz

Check that the directory is located directly below the Domoticz plugins directory:

```text
plugins/Domoticz-OpenAQ-Plugin/plugin.py
```

Restart Domoticz after correcting the installation, and run the syntax check from section 3.4.

### Python error during startup

Look in the Domoticz log for messages containing `OpenAQ`, `Traceback`, `ImportError`, `SyntaxError` or `IndexError`.

### Log messages

| Log message | Meaning and action |
|---|---|
| `Unable to read openaq API-KEY from settings` | The API key field is empty. Enter your key. |
| `OpenAQ Location ID must be numeric, got '<value>'` | Enter digits only, or leave the field blank. |
| `No OpenAQ location found within <n> m of <lat>,<lon>` | Nothing was found within the radius, or the lookup failed (check the error just before it). Increase the radius (up to 25 km) or enter a Location ID. |
| `OpenAQ request <path> returned HTTP <status>: ...` | OpenAQ rejected a startup request. Check that the API key is valid and, for the automatic lookup, that the radius is 25 km or less. |
| `OpenAQ request <path> failed: <error>` | Network, DNS, TLS or timeout problem between Domoticz and `api.openaq.org`. Check connectivity from the Domoticz host. |
| `Could not fetch sensor list for OpenAQ location <id>` | The location lookup failed. Check the ID and look for an earlier error. |
| `Location <id> returned no usable sensors` | The location has no sensors the plugin can use. Choose another location. |
| `openaq returned HTTP 410 Gone ...` | OpenAQ retired an API path that this version uses. Update the plugin. |
| `openaq returned a status: 401` or `403` | Normally means the API key is missing, invalid or revoked. Check it on your OpenAQ account page. |
| `openaq returned a status: 429` | OpenAQ rate limit reached. Wait for the next period; avoid restarting the plugin repeatedly. |
| `IndexError: list index out of range` at startup | The Domoticz location is not set. See section 4. |
| `AttributeError: 'NoneType' object has no attribute 'Connecting'` on every heartbeat | A consequence of an earlier startup failure. Find and fix the first error in the log; this is a known defect (see `ROADMAP.md`). |

### Devices exist but show no values

1. Open the **Info** device. `Sensors reporting: 0/<m>` means OpenAQ returned no usable readings for the tracked pollutants.
2. Remember that readings of zero or below are ignored, and that sensors for other parameters are not shown.
3. Check on <https://explore.openaq.org> that the location currently reports the pollutants you expect.
4. Enable Debug briefly (and redact the log before sharing it, see section 11).

### Air quality level looks wrong

The limits are in µg/m³ and the plugin does not convert units. If your location reports a different unit, the level is not meaningful. See the Known limitations in `README.md`.

---

## 13. Removing the plugin

Before removing the plugin, disable or delete the hardware instance in Domoticz. Then remove the plugin directory:

```bash
sudo rm -rf /opt/domoticz/plugins/Domoticz-OpenAQ-Plugin
sudo systemctl restart domoticz
```

Plugin-created devices are removed together with the hardware instance. Check **Setup → Devices** for leftovers.

---

## 14. Docker deployment

For a Docker-based Domoticz installation, the plugin must be available inside the Domoticz container. A typical setup keeps the plugins on a host-mounted volume:

```text
Host
└── domoticz/
    └── plugins/
        └── Domoticz-OpenAQ-Plugin/
```

After installing or updating the plugin on the host-mounted directory:

```bash
docker restart domoticz
docker exec -it domoticz ls -la /opt/domoticz/plugins/Domoticz-OpenAQ-Plugin
```

The exact container path depends on the Domoticz Docker image and your volume configuration. The container needs outbound HTTPS access to `api.openaq.org`.

---

## 15. Production deployment checklist

Before considering a deployment complete:

* [ ] Domoticz is running correctly.
* [ ] The Domoticz location is set.
* [ ] `plugin.py` and `xfr_openaq2.zip` are in the correct `plugins` directory.
* [ ] File permissions allow Domoticz to read the plugin.
* [ ] The **OpenAQ 4** hardware instance exists and is enabled.
* [ ] The API key is entered only in the Domoticz hardware form.
* [ ] A Location ID is set (or the auto-selected one has been copied into the field).
* [ ] Debug is set to `False`.
* [ ] Ten devices appear under **Setup → Devices**.
* [ ] The Info device shows the expected location ID.
* [ ] No errors or Python exceptions appear in the Domoticz log.
* [ ] The deployed Git version or tag has been recorded.

---

## 16. Security

Security-related information and vulnerability reporting are documented separately in:

```text
SECURITY.md
```

Never commit your API key to this repository. If it has been committed by accident, treat it as compromised: rotate it on your OpenAQ account page first, then remove it from the Git history.

---

## 17. Support

For problems with the plugin, provide:

* plugin version
* Domoticz version
* Python version
* operating system
* installation type (native or Docker)
* the OpenAQ location ID, and whether it was entered or auto-detected
* relevant Domoticz log entries, with the API key and your location removed

Project repository:

<https://github.com/janreimen/Domoticz-OpenAQ-Plugin>
