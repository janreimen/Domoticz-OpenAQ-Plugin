# Deployment Guide

## Domoticz OpenAQ Plugin

This document describes how to install, configure, update, and remove the **OpenAQ 4** plugin for Domoticz.

The plugin reads the latest air-quality measurements for one or more OpenAQ locations ("stations", up to 20) and exposes them as Domoticz devices, ten per station. It is read-only: it only sends `GET` requests to OpenAQ.

**Supported versions:** 4.2 and 4.1. Support for versions before 4.2 will end in a future release, so plan to be on 4.2 (see section 9). Versions before 4.1 are not supported.

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

The free tier allows 60 requests per minute and 2,000 per hour. The plugin polls every station about once per hour (one request per station), plus one short lookup per station each time it starts, and one more search if it has to find the stations automatically. With 20 stations that is at most 21 requests at startup and 20 per hour.

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

## 4. Set your location in Domoticz (only for the automatic lookup)

Open **Setup → Settings → System → Location** and make sure your latitude and longitude are set, if you want the plugin to find stations for you (section 5).

The plugin reads these coordinates only for the automatic location lookup. If you enter OpenAQ Location IDs, the Domoticz location is not read and may stay empty. Your coordinates are sent to OpenAQ only in that one lookup request, and are never written to the Domoticz log.

---

## 5. Choose the OpenAQ stations

Check <https://explore.openaq.org> to see which locations exist near you, and which pollutants they report. OpenAQ calls a station a *location*; its ID is the number in the address of its page.

You have two options:

* **Enter Location IDs** (recommended). A comma-separated list such as `9645,1140`, or a single ID. The plugin reads exactly those stations, up to 20. Spaces are ignored; an entry that is not a positive whole number is ignored and reported; a duplicate is used once.
* **Leave the field blank.** The plugin asks OpenAQ once at startup for the locations within the radius of your Domoticz coordinates, ranks them by distance itself, and uses the nearest *Max auto-detected stations* of them (default 3). It logs what it did:

```text
Looking for up to 3 OpenAQ location(s) within 10 km of the Domoticz location
OpenAQ returned 7 location(s); the nearest ones are used.
Auto-detected OpenAQ location 1 of 3: '<name>' (id <id>)
Auto-detected OpenAQ location 2 of 3: '<name>' (id <id>)
Auto-detected OpenAQ location 3 of 3: '<name>' (id <id>)
To keep using the auto-detected location(s), enter <id1>,<id2>,<id3> in the 'OpenAQ Location IDs' hardware field.
```

How the automatic choice works:

* OpenAQ's API documentation does not state that results are ordered by distance, so the plugin ranks them. It uses the coordinates OpenAQ gives for each location, then OpenAQ's `distance` field, and if neither is usable it keeps OpenAQ's order and says so (`... without usable distances; the first ones returned are used, which are not necessarily the nearest.`).
* Only the first 100 locations OpenAQ returns are ranked. If that page is full the log says so; use a smaller radius to rank all locations.
* **Stations that already have devices come first** (the log adds `, kept because it already has devices`), so their history continues when a nearer station appears. The remaining places go to the nearest others.
* After the first start, copy the logged IDs into the **OpenAQ Location IDs** field to make the choice permanent.

The radius is in kilometres, defaults to `10`, and is only used for the automatic lookup. Decimals are accepted (`2.5` or `2,5`). OpenAQ documents a maximum of 25 km: a larger value is reduced to 25, and an empty, non-numeric or non-positive value uses `10`. The log always states the radius that was used.

**Max auto-detected stations** is a whole number from 1 to 20. A value above 20 is reduced to 20, and a value that is not a positive whole number uses `3`. An **empty** field means `1`: that is what an instance created by 4.1 contains until its hardware page is saved, and it keeps an upgrade from adding devices (see section 9).

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
| Radius (km, max 25, used only if Location IDs is blank) | `10`, or up to `25` |
| OpenAQ API-KEY | Your OpenAQ API key (the field is masked) |
| OpenAQ Location IDs, comma separated (blank = auto-detect) | One or more numeric location IDs, for example `9645,1140`, or blank |
| Max auto-detected stations | `3` (default). Only used when the Location IDs are blank |
| Debug | `False` for normal use |

**Do not put the API key into `plugin.py`.** Enter it only in the Domoticz hardware form.

Keep **Debug** set to `False` unless you are diagnosing a problem. Debug output is verbose. The API key and your Domoticz location are masked in it (see section 11).

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
2. Open **Setup → Devices** and verify that every station has ten devices: BC, CO, NO<sub>2</sub>, O<sub>3</sub>, PM<sub>10</sub>, PM<sub>2.5</sub>, SO<sub>2</sub>, Info, Pollutants and Air Quality Index. With one station the names are plain; with several the station is added, for example `PM2.5 (Beidweiler)`.
3. Check the **Info** device of each station. It should read `OpenAQ Location ID: <id>` and `Sensors reporting: <n>/<m>`.
4. Check the Domoticz log for plugin errors.

Station devices are numbered in blocks of ten: the first station uses units 1–10, the second 11–20, the third 21–30, and so on up to units 191–200. The **Setup → Devices** page shows the unit (the *Idx* is Domoticz's own number).

At the normal log level a successful start is quiet. With blank Location IDs the plugin writes the lookup and auto-detection lines described in section 5; with Location IDs it writes nothing. The devices are created at startup and receive their values after the first successful poll.

If some stations could not be set up, the log says `OpenAQ started with <k> of <n> stations` and lists the failed IDs, preceded by the reason for each. The others run. If none could be set up, or the settings are invalid, the log ends with `OpenAQ plugin not started`, preceded by the reason, and the plugin stays idle until you restart or update the hardware (see section 12).

A pollutant device only receives a value if the location reports that pollutant. Devices for pollutants your location does not measure stay empty. This is normal.

### How stations and devices are matched

The position of an ID in the list does not matter. New devices carry a tag with the location ID (Domoticz *DeviceID*, for example `OAQ9645-6` for the PM<sub>2.5</sub> device of station 9645). At every start the plugin gives each station the block that carries its ID, and the lowest free block to a station that has none. Therefore:

* **Reordering the list, removing an ID, or a temporary failure** of one station never puts another station's readings on a station's devices.
* **Devices created by 4.0 and 4.1** carry no tag. Their station is identified from the **Info** device (`OpenAQ Location ID: <id>`, written by every poll), so units 1–10 stay with the station they were created for. **Do not edit the text of an Info device.** If those devices were never updated, they go to the first station without a block, because they hold no history.
* **A station you remove keeps its block reserved.** Its devices stay in Domoticz, unchanged, with their history, and every start logs `Units <a>-<b> belong to OpenAQ location <id>, which is not in use now`. Adding the ID again continues the history. To free the block, delete those devices in **Setup → Devices**.
* **20 blocks are the limit.** Reserved blocks count. A further station is skipped with `No free slot for OpenAQ location <id>`.
* **Names are set once.** The plugin never renames a device; you may rename devices freely in Domoticz.

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
git checkout 4.2
```

Releases are tagged with the plain version number (`4.1`, `4.2`); only the older 4.0 tag carries a prefix (`v4.0`).

Restart Domoticz:

```bash
sudo systemctl restart domoticz
```

### Upgrading from 4.1 (or 4.0)

Update the plugin and restart Domoticz. No other action is needed: the plugin key is unchanged, the hardware instance keeps its settings, and the devices keep their unit numbers and history.

* A single Location ID is a list of one. Its station keeps units 1–10 and its devices keep their names.
* The field is now called **OpenAQ Location IDs** and accepts a list. **Max auto-detected stations** is new.
* If the instance uses the automatic lookup, **Max auto-detected stations** is empty until you save the hardware page, and an empty field means 1, as in 4.1. The plugin keeps the station that your devices already belong to (even if a nearer one exists), so **no devices are added**. To use more stations, enter a number (the form offers `3`) or, better, enter the IDs that the log suggests.
* Adding a station creates ten new devices for it, named with the station. The devices of the first station keep their plain names.
* If you came from 4.0, the **OpenAQ API-KEY** field is now masked, and you should rotate your API key if you ever shared a Debug log written by 4.0 or earlier (see section 11).

### Going back to 4.1

4.1 knows only one station. Before you check out 4.1:

1. Reduce **OpenAQ Location IDs** to a single ID, the one whose devices are units 1–10 (its Info device says which). 4.1 stops with `OpenAQ Location ID must be numeric` on a list, and does not touch any device.
2. Check out the 4.1 tag (`git checkout 4.1`) and restart Domoticz.

The devices of the other stations stay in Domoticz and keep their last values, but 4.1 does not update them. Going forward to 4.2 again finds them through their tags, and continues their history.

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

**Since 4.1 the plugin never writes your OpenAQ API key or your Domoticz location to the log, even with Debug enabled.** The Debug dump of the hardware parameters and Domoticz settings looks like this:

```text
Parameter 'Mode1'...: '10'
Parameter 'Mode2'...: '<masked>'
Setting 'Location'...: '<masked>'
```

Only an allow-list of harmless values (radius, location ID, Debug setting, Domoticz's own folders and version) is shown; everything else is `<masked>`, or `<empty>` when blank. Request paths are logged without their query string, and the key is scrubbed from any logged response or error text.

Still review a log before sharing it, because it contains other information about your system, and Domoticz writes its own lines which the plugin cannot control. Also avoid posting:

* Your API key
* Your Domoticz location coordinates
* Any other credentials or personal data

**Older versions leak.** Versions up to 4.0 write the key and your location to the Debug log (and your coordinates to the normal log when the automatic lookup fails). If you ever shared a log from such a version, rotate the key on your OpenAQ account page (<https://explore.openaq.org/account>) and enter the new key in the Domoticz hardware form.

Turn Debug off again when you are done.

---

## 12. Troubleshooting

### Plugin does not appear in Domoticz

Check that the directory is located directly below the Domoticz plugins directory:

```text
plugins/Domoticz-OpenAQ-Plugin/plugin.py
```

Restart Domoticz after correcting the installation, and run the syntax check from section 3.4.

### Python error during startup

Look in the Domoticz log for messages containing `OpenAQ`, `Traceback`, `ImportError` or `SyntaxError`.

### Some stations are missing

If the log says `OpenAQ started with <k> of <n> stations`, the stations it lists could not be set up. The errors just before that line say why for each of them (unknown ID, no usable sensors, OpenAQ error). The others run. If the cause was temporary, restart the hardware: the missing stations then take their own devices, whatever the order of the list.

If the log says `Skipping the remaining <n> OpenAQ location(s): ...`, the first failing request showed that the others would fail too, so they were not tried: OpenAQ could not be reached, your API key was rejected (check it), or the rate limit was reached (wait a while before restarting).

### The plugin did not start

Every failed start ends with this line, preceded by the specific reason (see the table below):

```text
OpenAQ plugin not started - see the message above. It stays idle until the hardware is restarted or updated.
```

The plugin then ignores heartbeats and creates no devices or connections. Fix the cause, then restart or update the hardware under **Setup → Hardware**.

The startup lookups are not retried automatically. If the cause was temporary (for example Domoticz started before the network was up), restarting the hardware is enough.

### Log messages

| Log message | Meaning and action |
|---|---|
| `Unable to read openaq API-KEY from settings` | The API key field is empty. Enter your key. |
| `OpenAQ Location ID '<value>' is not a positive whole number and is ignored` | One entry of the list is not a positive whole number (zero and negative IDs do not exist). The other entries are used. Correct the entry. |
| `OpenAQ Location ID <id> is listed more than once; using it once` | Informational. Remove the duplicate. |
| `Too many OpenAQ Location IDs (<n> entered); only the first 20 are used` | At most 20 stations are supported. |
| `No valid OpenAQ Location ID entered` | The field is not blank but holds no usable ID. Enter numbers separated by commas, or leave the field blank to auto-detect. |
| `The Domoticz location is not set or not valid` | Only raised for the automatic lookup. Set **Setup → Settings → System → Location**, or enter Location IDs. See section 4. |
| `Radius '<value>' is not a positive number; using the default of 10 km` | Informational. The radius field is not a positive number, so the default is used. |
| `Radius is empty; using the default of 10 km` | Informational. |
| `Radius <value> km is above the OpenAQ maximum of 25 km; using 25 km` | Informational. OpenAQ accepts at most 25 km. |
| `Max auto-detected stations is empty; using 1` | Informational. An empty field means 1 station (this is what a 4.1 instance has until its hardware page is saved). Enter a number to detect more. |
| `Max auto-detected stations '<value>' is not a positive whole number; using the default of 3` | Informational. Enter a whole number from 1 to 20. |
| `Max auto-detected stations <n> is above the maximum of 20; using 20` | Informational. |
| `No OpenAQ location found within <n> km of the Domoticz location` | Nothing was found within the radius, or the lookup failed (check the error just before it). Increase the radius (up to 25 km) or enter Location IDs. |
| `OpenAQ returned a location without an ID` | OpenAQ's answer was unusable. Enter Location IDs. |
| `OpenAQ request <path> returned HTTP <status>: ...` | OpenAQ rejected a startup request. Check that the API key is valid and that the Location ID exists. The logged path never contains your coordinates. |
| `OpenAQ request <path> failed: <error>` | Network, DNS, TLS or timeout problem between Domoticz and `api.openaq.org`. Check connectivity from the Domoticz host. |
| `Could not fetch sensor list for OpenAQ location <id>` | The lookup of that station failed. Check the ID and look for an earlier error. |
| `Location <id> returned no usable sensors` | The location has no sensors the plugin can use. Choose another location. |
| `Skipping the remaining <n> OpenAQ location(s): <reason>` | A request showed that every further request would fail as well, so the remaining stations were not tried. Reasons: OpenAQ could not be reached; the API key was rejected (HTTP 401 or 403: check it on your OpenAQ account page); the rate limit was reached (HTTP 429: wait before restarting). |
| `No free slot for OpenAQ location <id>` | All 20 station blocks are in use, including those reserved for stations that are no longer configured. Delete the devices of a station you no longer use in **Setup → Devices** to free its block. |
| `Units <a>-<b> belong to OpenAQ location <id>, which is not in use now` | Informational. The devices of that block are left unchanged and keep their history. Add the ID again to continue them, or delete the devices to free the block. |
| `OpenAQ started with <k> of <n> stations` | Some stations could not be set up; the message lists them. See "Some stations are missing". |
| `OpenAQ plugin not started` | No station could be set up, or the settings are invalid. See "The plugin did not start". |
| `openaq_<id> returned HTTP 410 Gone ...` | OpenAQ retired an API path that this version uses. Update the plugin. |
| `openaq_<id> returned a status: <status>` | OpenAQ answered the hourly poll of that station with an error. 401 or 403 normally mean the API key is missing, invalid or revoked: check it on your OpenAQ account page. 429 means the rate limit was reached: wait for the next period, and avoid restarting the plugin repeatedly. |

### Devices exist but show no values

1. Open the **Info** device of that station. `Sensors reporting: 0/<m>` means OpenAQ returned no usable readings for the tracked pollutants.
2. Remember that readings of zero or below are ignored, and that sensors for other parameters are not shown.
3. Check on <https://explore.openaq.org> that the location currently reports the pollutants you expect.
4. Enable Debug briefly (and review the log before sharing it, see section 11).

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
* [ ] The Domoticz location is set (only needed if the Location IDs are left blank).
* [ ] `plugin.py` and `xfr_openaq2.zip` are in the correct `plugins` directory.
* [ ] File permissions allow Domoticz to read the plugin.
* [ ] The **OpenAQ 4** hardware instance exists and is enabled.
* [ ] The API key is entered only in the Domoticz hardware form.
* [ ] Location IDs are set (or the auto-detected ones have been copied into the field).
* [ ] Debug is set to `False`.
* [ ] Ten devices per station appear under **Setup → Devices**.
* [ ] The Info device of each station shows the expected location ID.
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
* the OpenAQ location ID(s), and whether they were entered or auto-detected
* relevant Domoticz log entries, with the API key and your location removed

Project repository:

<https://github.com/janreimen/Domoticz-OpenAQ-Plugin>
