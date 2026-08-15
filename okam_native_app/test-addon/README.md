# Testing a candidate build beside the installed add-on

This is a **local** Home Assistant add-on that runs a candidate image without
touching the installed O-KAM Native Bridge. It differs from the production
add-on in every way that would otherwise collide:

| | Installed add-on | This test add-on |
| --- | --- | --- |
| Slug | `okam_native` | `okam_native_test` |
| Host port | 8099 | 8098 |
| Start on boot | auto | manual |
| Image tag | the released version | `1.2.0-test1` |

Because the slug and port differ, both can be installed at once, and removing
this one leaves the installed add-on untouched.

## Install

1. Copy this directory to the Pi as `/addons/okam_native_test/`, so that
   `/addons/okam_native_test/config.yaml` exists. The Samba or the file editor
   add-on both work.
2. Settings, Add-ons, Add-on store, three-dot menu, Check for updates.
3. The add-on appears under *Local add-ons*. Install it.
4. Fill in the options. Use the **same account credentials** as the installed
   add-on, a **different** `api_token`, and leave `camera_id` as `cabintest` so
   the entity cannot collide with the existing one.
5. Start it and watch the log.

## What a healthy start looks like

```
native_loader_ready=true
account_enumerated=true device_count=1
bridge_ready=true camera_count=1
```

## Point Home Assistant at it

Add the integration a second time: Settings, Devices and services, Add
integration, O-KAM, then set the bridge URL to `http://<pi-address>:8098` and
the `api_token` you configured here.

## Remove afterwards

Stop and uninstall the add-on, delete `/addons/okam_native_test/`, and remove
the extra integration entry. The installed add-on and its entity are unaffected
throughout.

## One camera, two clients

The camera admits a limited number of concurrent sessions. Stop the installed
add-on while testing this one, or expect both to intermittently fail to
connect while they compete.
