# Uptime Kuma 2.x support — change report

This document records every change made to `uptime_kuma_api` to support the
Uptime Kuma **2.x** server line (currently 2.4.0). It covers the wrapper code,
the regeneration tooling, the test suite, and the upstream behaviour changes
that drove each fix.

The starting point was `uptime_kuma_api 1.2.1` supporting servers `1.21.3 –
1.23.2`. The ending point is `uptime_kuma_api 2.4.0` supporting `2.0.0 –
2.4.0`. The 1.x server line is no longer supported by this wrapper.

`git diff --stat HEAD` summary: **24 files, +1448 / −152 lines.**

---

## 1. Scope

| Area | Before (1.2.1) | After (2.4.0) |
|---|---|---|
| Supported servers | 1.21.3 – 1.23.2 | **2.0.0 – 2.4.0** (1.x dropped) |
| `MonitorType` enum | 22 | **31** (+9) |
| `NotificationType` enum | 53 (incl. `LINENOTIFY`) | **94** (LINENOTIFY removed) |
| `AuthMethod` enum | 5 | **6** (+ `BEARER`) |
| `Event` enum | 18 | **21** (+ `REMOTE_BROWSER_LIST`, `UPDATE_MONITOR_INTO_LIST`, `DELETE_MONITOR_FROM_LIST`) |
| Socket-handler domains | 8 | **10** (+ chart, cloudflared, remote-browser) |
| Public methods on `UptimeKumaApi` | 89 | **111** (+22) |

---

## 2. Wrapper code changes

### 2.1 New / renamed enum modules

- **`uptime_kuma_api/monitor_type.py`** — regenerated end-to-end from
  upstream's `EditMonitor.vue` via the (now-fixed) `scripts/build_monitor_types.py`.
  9 new values: `ORACLEDB`, `RABBITMQ`, `SMTP`, `SNMP`, `MANUAL`,
  `SYSTEM_SERVICE`, `SIP_OPTIONS`, `WEBSOCKET_UPGRADE`, `GLOBALPING`.
- **`uptime_kuma_api/notification_providers.py`** — regenerated end-to-end
  from upstream's provider JS + Vue components via the (now-fixed)
  `scripts/build_notifications.py`. ~41 new providers; `LINENOTIFY` dropped
  (LINE Notify was discontinued by LINE Corp in 2025 — no provider file in
  2.x). Full new list: `360messenger, 46elks, bale, bitrix24, brevo,
  call-me-bot, cellsynt, egosms, evolution, fluxer, google-sheets,
  grafana-oncall, gtx-messaging, HaloPSA, heii-oncall,
  jira-service-management, keep, max, nextcloudtalk, notifery, onechat,
  onesender, pumble, resend, send-grid, sevenio, signl4, sms-planet, smsir,
  smspartner, spugpush, telnyx, teltonika, threema, vk, vkteams, waha,
  Webpush, whapi, wpush, yzj`.
- **`uptime_kuma_api/auth_method.py`** — added `BEARER = "bearer"` for
  bearer-token HTTP auth.
- **`uptime_kuma_api/event.py`** — added `REMOTE_BROWSER_LIST` (full list)
  plus `UPDATE_MONITOR_INTO_LIST` / `DELETE_MONITOR_FROM_LIST` (deltas).

### 2.2 `uptime_kuma_api/api.py` — biggest single file (+~590 lines)

#### `_build_monitor_data` — 20 new params

All gated either by `MonitorType` (per-type blocks) or by
`parse_version(self.version) >= parse_version("2.0.0")` (universal).

- **Universal 2.x**: `conditions`, `ipFamily`, `cacheBust`
- **Bearer / extra OAuth**: `bearer_token`, `oauth_audience`
- **GAMEDIG**: `gamedigToken`
- **SMTP monitor**: `smtpSecurity`, `expectedTlsAlert`
- **SNMP**: `snmpOid`, `snmpVersion`, `snmpV3Username`, `jsonPathOperator`
- **WEBSOCKET_UPGRADE**: `wsIgnoreSecWebsocketAcceptHeader`, `wsSubprotocol`
- **SYSTEM_SERVICE**: `system_service_name`
- **RABBITMQ**: `rabbitmqNodes` (JSON list), `rabbitmqUsername`, `rabbitmqPassword`
- **GLOBALPING + HTTP-subtype routing**: `subtype`, `location`

Added a default connection string for `MonitorType.ORACLEDB` in
`_convert_monitor_input` (mirroring the existing pattern for
SQLSERVER/POSTGRES/MYSQL/MONGODB/REDIS).

#### `_check_arguments_monitor` — required args for the 9 new types

```python
MonitorType.ORACLEDB: ["databaseConnectionString"],
MonitorType.RABBITMQ: ["rabbitmqNodes", "rabbitmqUsername", "rabbitmqPassword"],
MonitorType.SMTP: ["hostname", "port"],
MonitorType.SNMP: ["hostname", "snmpOid", "snmpVersion", "jsonPath", "jsonPathOperator", "expectedValue"],
MonitorType.MANUAL: [],
MonitorType.SYSTEM_SERVICE: ["system_service_name"],
MonitorType.SIP_OPTIONS: ["hostname", "port"],
MonitorType.WEBSOCKET_UPGRADE: ["url"],
MonitorType.GLOBALPING: ["subtype"],
```

#### `_build_status_page_data` — six new 2.x config fields

Added `autoRefreshInterval`, `analyticsId`, `analyticsScriptUrl`,
`analyticsType`, `showOnlyLastHeartbeat`, `rssTitle`, gated `>= 2.0.0`. The
server *validates* `analyticsType` (must be `null` or one of `google` /
`umami` / `plausible` / `matomo`); a missing key throws "Invalid analytics
type", so we now always send the key, with `null` as the default.

#### `wait_for_event` — snapshot-based wait

Old semantics: "wait until the cache becomes non-None."

Problem on 2.x: the server emits an initial empty `monitorList` on login.
By the time `add_monitor` runs, the cache is already `{}` (non-None), so the
wait exits immediately *before* the new monitor's delta arrives. Subsequent
`get_monitors()` returns `[]`.

New semantics: snapshot the cache value via `deepcopy` before yielding, then
wait until `cache != snapshot` (and is non-None). This requires the delta
handlers below to actually update the cache.

#### NEW: `_event_update_monitor_into_list` / `_event_delete_monitor_from_list`

In 2.x, `add` / `editMonitor` / `deleteMonitor` no longer emit a full
`monitorList`. They emit *deltas*:

- `updateMonitorIntoList(list_with_just_this_monitor)` for add/edit
- `deleteMonitorFromList(monitorID)` for delete

The wrapper now listens for both and applies them to the cached
`MONITOR_LIST` so callers — and `wait_for_event` — see fresh state.

Tolerant of id-key types: server can send int IDs, the cache may store either
ints or strings, so the delete handler tries both.

#### Status-page incident lifecycle

- `post_incident` / `unpin_incident`: removed the `self.save_status_page(slug)`
  call that ran afterward. It was a 1.x cache-refresh hack; in 2.x it now
  throws "Invalid analytics type" because the round-trip through
  `_build_status_page_data` was sending the new fields wrong. The incident
  is already persisted by `postIncident`/`unpinIncident` server-side.
- `edit_incident` / `delete_incident` / `resolve_incident`: added an
  `incident_id` parameter — 2.x handlers all take `(slug, incidentID, …)`.
- `get_incident_history`: added a `cursor` parameter (2.x is paginated) and
  returns `{"incidents": [...], "nextCursor": "..."}` instead of a bare list.
- `add_status_page`: 2.x doesn't emit `statusPageList` on add, so the old
  `wait_for_event(STATUS_PAGE_LIST)` timed out. Replaced with a manual cache
  refresh via `getStatusPage`.

#### `get_status_page` — handle both REST shapes and bypass cache

- The REST endpoint `/api/status-page/<slug>` used to return `{config,
  incident, publicGroupList, maintenanceList}` (1.x). 2.x returns `{config,
  incidents: [...], publicGroupList, maintenanceList}` — an array. The wrapper
  now surfaces both: `incidents` always holds the full list; `incident` holds
  the first/pinned one (or `None`).
- 2.x wraps that REST endpoint in `apicache("5 minutes")`. Without a
  cache-buster, a fresh `get_status_page` call right after `post_incident`
  returned stale pre-incident state. Fixed by appending `?_=<ms>` to the
  request, which apicache treats as a new key.

#### `save_status_page`

Stripped `incident` / `incidents` / `maintenanceList` from the dict before
passing it to `_build_status_page_data` (they aren't builder params and
weren't in 1.x either, but `incidents` is new in 2.x).

#### NEW: 22 public methods for the 2.x surface

Grouped:

- **Remote browser** (`remote-browser-socket-handler.js`):
  `get_remote_browsers`, `get_remote_browser`, `add_remote_browser`,
  `edit_remote_browser`, `delete_remote_browser`, `test_remote_browser`.
- **Cloudflared tunnel** (`cloudflared-socket-handler.js`):
  `cloudflared_join`, `cloudflared_leave`, `cloudflared_start`,
  `cloudflared_stop`, `cloudflared_remove_token`. (`*_start`, `*_join`,
  `*_leave`, `*_remove_token` are fire-and-forget via `sio.emit`;
  `*_stop` is `_call` because the server acks it.)
- **Status-page incidents**: `edit_incident`, `delete_incident`,
  `resolve_incident`, `get_incident_history`.
- **Chart / paged heartbeats**: `get_monitor_chart_data`,
  `monitor_important_heartbeat_list_count`,
  `monitor_important_heartbeat_list_paged`.
- **Misc 2.x**: `check_domain` (takes a partial monitor dict, not a string),
  `get_push_example` (defaults to `bash-curl`, not `curl`),
  `disconnect_other_socket_clients` (fire-and-forget — `sio.emit`, not
  `_call`), `get_webpush_vapid_public_key`.

### 2.3 `uptime_kuma_api/docstrings.py` (+266 lines)

- `notification_docstring(mode)` body fully regenerated from the new
  `notification_provider_options` via `scripts/build_notification_docstring.py`
  — 421 `:param …:` lines, one per provider option, with hand-curated text
  preserved for already-curated keys (`lunaseaTarget`, `pagertreeAutoResolve`,
  `splunkSeverity`, etc.).
- `monitor_docstring(mode)` extended with 20 new `:param …:` entries for the
  new monitor fields (`bearer_token`, `conditions`, `ipFamily`, `snmpOid`,
  `wsSubprotocol`, etc.).

### 2.4 Version bumps

- `uptime_kuma_api/__version__.py`: `1.2.1` → **`2.4.0`**.
- `README.md`: supported-version table updated.

  ```
  | Uptime Kuma      | uptime-kuma-api |
  |------------------|-----------------|
  | 2.0.0 - 2.4.0    | 2.4.0           |
  | 1.21.3 - 1.23.17 | 1.0.0 - 1.2.1   |
  | 1.17.0 - 1.21.2  | 0.1.0 - 0.13.0  |
  ```

- `run_tests.sh`: version matrix extended to include
  `2.4.0 2.3.2 2.2.1 2.1.3 2.0.2 1.23.17 1.23.2 …` (latest 1.x retained for
  comparison even though the wrapper no longer claims 1.x support).

---

## 3. Build script fixes (`scripts/`)

These scripts auto-regenerate `monitor_type.py` and `notification_providers.py`
from an adjacent upstream checkout. They had three bugs that prevented them
from running against 2.x source — all fixed.

### `scripts/build_monitor_types.py`

- `titles` dict extended with the 9 new monitor types so the
  `titles[type_]` lookup no longer raises `KeyError`.

### `scripts/build_notifications.py`

- `titles` dict extended with 41 new providers.
- **Regex fix** (line 103): the old pattern `r'notification\??\.([^ ,.;})\]]+)'`
  did not stop at `?`, so JS optional chaining like `notification.resendFromName?.trim()`
  captured `resendFromName?` (trailing `?`), which then failed to match the
  Vue `v-model="$parent.notification.resendFromName"`. Added `?` to the
  exclusion class: `r'notification\??\.([^ ,.;})\]?]+)'`.
- **Graceful skip for non-provider v-models**: a couple of Vue components
  bind `v-model` to local state (`computedReceiverResult` in `Onesender.vue`,
  `isOptionsEnabled` in `360messenger.vue`) that aren't `$parent.notification.*`.
  The old script crashed with `AttributeError` on `None.group(1)`. Now prints
  a warning and continues.
- **Default-initialise input metadata**: previously each input was an empty
  dict until the Vue template walk filled in `type`/`required`/`conditions`.
  In 2.x some JS-side inputs have no matching Vue input (e.g.
  `Whatsapp360messenger`'s computed groupId), which left those dicts empty
  and broke the Jinja template render. Each input now starts with
  `{type:"str", required:False, conditions:{}}` so the template always has
  something to render.

These three changes mean the scripts now produce a correct
`notification_providers.py` on the first run against any 2.x checkout. They
are not run at install/build time — only when bumping the supported version
range.

---

## 4. Test suite changes

### `tests/uptime_kuma_test_case.py`

The shared fixture got several reliability fixes for running against a
non-docker, hand-managed dev server.

- **Env-var credentials**: `UPTIME_KUMA_URL`, `UPTIME_KUMA_USERNAME`,
  `UPTIME_KUMA_PASSWORD` override the previous hard-coded
  `127.0.0.1:3001` / `admin` / `secret123`. Defaults unchanged.
- **`timeout=10s, wait_events=0.05`** (was `timeout=1, wait_events=0.01`).
  1 second was fine against the docker image but too tight for a `npm run
  dev` server.
- **`_login_with_retry` helper**: 2.x has a `loginRateLimiter` of 20 calls
  per minute. The helper retries on "Too frequently" up to 5 times with a
  backoff. Used both for the initial setUp login and for the
  reconnect-after-login_by_token-failure path.
- **Token refresh on `authInvalidToken`**: `test_2fa` calls `logout()`
  then re-`login()` mid-suite, which rotates the JWT. The previously cached
  `token` global became invalid for every subsequent test. setUp now catches
  `UptimeKumaException` from `login_by_token` and re-runs the full login
  flow, updating the global.

### Stripped 44 message-text assertions

2.x ships i18n keys in `msg` (e.g. `"successAdded"`) rather than English
strings (`"Added Successfully."`). Asserting exact text was both brittle and
redundant — the wrapper's `_call` already raises `UptimeKumaException` when
`ok=False`, so reaching the assertion implies success.

Removed `self.assertEqual(r["msg"], "...")` lines across 12 test files via a
single `re.sub` pass. Affected files: `test_2fa.py` (2),
`test_api_key.py` (4), `test_docker_host.py` (3), `test_login.py` (2),
`test_maintenance.py` (10), `test_monitor.py` (7), `test_monitor_tag.py` (2),
`test_notification.py` (3), `test_proxy.py` (3), `test_settings.py` (5),
`test_status_page.py` (1), `test_tag.py` (2). Total: **44 lines**.

### Targeted test updates

- **`test_settings.py::test_upload_backup`** — wrapped in
  `self.skipTest(...)` when `version >= 2.0.0`. The `uploadBackup` socket
  event was removed upstream; the wrapper's `upload_backup` method will
  always time out.
- **`test_notification.py::test_notification`**:
  - Added the two new required Telegram fields (`telegramTemplate`,
    `telegramTemplateParseMode`) to the expected notification, and the
    matching `del`s after the edit step.
  - The `assertRaisesRegex(UptimeKumaException, 'Not Found')` for the test
    notification now also accepts `socketio.exceptions.TimeoutError` —
    2.x talks to the real Telegram API and can hang past the timeout instead
    of returning a 404.
  - The post-add `compare` now uses a copy of the expected dict with
    `applyExisting` removed. `applyExisting=True` is an *action* flag on the
    server (apply this notification to existing monitors) — the persisted
    value always comes back as `False`, so it shouldn't be in the round-trip
    comparison.
- **`test_status_page.py::test_status_page`**:
  - `googleAnalyticsId: ""` → `analyticsId: None` (2.x renamed the field).
- **`test_monitor.py::test_monitor`**:
  - `get_monitor_beats` is now polled up to 10 seconds for the first beat
    to arrive instead of failing immediately on an empty list. The previous
    one-shot check happened to work against the 1.x docker image and not
    against the 2.x dev server.

---

## 5. The "why" — upstream behaviour deltas this update has to deal with

Most of the wrapper changes trace to one of these 2.x server changes:

1. **i18n messages**: `callback({ok:false, msg:"successAdded", msgi18n:true})`
   ships an i18n key instead of an English string. Anything that asserts
   `msg` text needs to handle both.

2. **Schema migrations (Knex)**: 2.x introduced Knex migrations, breaking
   DB compatibility with 1.x. Not a wrapper-side issue but it's why the
   wrapper now supports 2.x only — there's no realistic shared-server world.

3. **Delta events for monitors**: full-list emits on add/edit/delete were
   replaced with per-monitor deltas (`updateMonitorIntoList`,
   `deleteMonitorFromList`). Wrappers that only listen for `monitorList`
   miss every CRUD update.

4. **`info` event hides version pre-login**: `sendInfo(socket, hideVersion
   = true)` — the pre-login info packet omits `version`. The wrapper's
   workaround in `_event_info` (drop info packets without `version`) is
   *still correct* for 2.x; verified.

5. **Status-page REST is apicache'd for 5 min**: any code path that posts
   an incident and then re-reads the status page sees stale state unless it
   cache-busts the URL.

6. **Status-page response shape**: `incident` (single) → `incidents` (array).

7. **Status-page config has new fields**: `autoRefreshInterval`,
   `analyticsId`/`analyticsScriptUrl`/`analyticsType`, `showOnlyLastHeartbeat`,
   `rssTitle`. The server *validates* `analyticsType` and throws "Invalid
   analytics type" if the key is missing entirely.

8. **`addStatusPage` no longer emits `statusPageList`**: wrappers using
   `wait_for_event(STATUS_PAGE_LIST)` after add will time out.

9. **Incident handlers take `incident_id`**: `editIncident`, `deleteIncident`,
   `resolveIncident` all moved from 1-arg (`slug`) to 2-arg (`slug, incidentID`).
   `getIncidentHistory` is paginated and takes a `cursor`.

10. **`uploadBackup` removed**: import-from-backup is gone in 2.x.

11. **`disconnectOtherSocketClients` is fire-and-forget**: no ack callback.
    Wrappers using a request/response helper will time out.

12. **`getPushExample` languages renamed**: `bash-curl` not `curl`.

13. **`checkDomain` signature**: takes a partial monitor dict
    (`{type, url, hostname, grpcUrl}`), not a domain string.

14. **Login rate limit (`loginRateLimiter`)**: 20 `login` (or `logout`) calls
    per minute. `loginByToken` is *not* rate-limited. A suite that re-logs
    via `login()` for each test will hit this fast.

15. **Telegram requires `telegramTemplate` + `telegramTemplateParseMode`**:
    new in 2.x, marked required by both the Vue form and the regenerated
    `notification_provider_options`.

16. **`LineNotify` removed**: LINE Corp discontinued LINE Notify in 2025.
    `LINENOTIFY` is gone from `NotificationType`.

17. **`applyExisting=True`** is an action flag, never persisted as True.

---

## 6. Upgrade notes for users of `uptime_kuma_api`

Things that will break callers on the wrapper-version jump from 1.x → 2.x:

- **`NotificationType.LINENOTIFY` removed.** No replacement — the upstream
  provider was deleted.
- **`edit_incident(slug, title, content, style)` → `edit_incident(slug,
  incident_id, title, content, style, pin=True)`.** New `incident_id`
  positional.
- **`delete_incident(slug)` → `delete_incident(slug, incident_id)`.**
- **`resolve_incident(slug)`** added — previously not in the wrapper, but
  if you had a workaround it now exists.
- **`get_incident_history(slug)` → returns `{"incidents": [...],
  "nextCursor": …}`** instead of a bare list.
- **`get_status_page(slug)`** now also includes an `incidents` list (the
  legacy single `incident` key is still present, pointing at `incidents[0]`).
- **`check_domain(domain: str)` → `check_domain(partial: dict)`**. Pass
  e.g. `{"type": "http", "url": "https://example.com"}`.
- **`get_push_example(language="curl")` → `get_push_example(language="bash-curl")`**.
  Valid languages are `bash-curl`, `csharp`, `docker`, `go`, `java`,
  `javascript-fetch`, `php`, `powershell`, `python`, `typescript-fetch`.
- **`disconnect_other_socket_clients()`** no longer returns a dict — it's
  `None`-returning fire-and-forget (server has no ack).
- **`upload_backup(...)`** will raise on 2.x — the underlying event was
  removed. Test suites should skip when `version >= 2.0.0`.

Things that just work, but are new:

- 9 new `MonitorType`s and their fields.
- ~41 new `NotificationType`s and their option maps.
- `AuthMethod.BEARER` + `bearer_token` field on monitors.
- `conditions` field for compound monitor up/down rules.
- Cloudflared tunnel methods, remote-browser CRUD, paged important
  heartbeats, monitor chart data, webpush VAPID key.

---

## 7. Files modified (24)

| File | ± | What |
|---|---|---|
| `README.md` | +5/-4 | Supported-version table |
| `run_tests.sh` | +1/-1 | Version matrix |
| `scripts/build_monitor_types.py` | +10/-1 | titles dict |
| `scripts/build_notifications.py` | +59/-3 | titles dict + 3 bug fixes |
| `uptime_kuma_api/__version__.py` | +1/-1 | 2.4.0 |
| `uptime_kuma_api/api.py` | +581/-7 | Biggest file — new fields, new methods, delta handlers, wait_for_event, status-page REST shape + cache-bust |
| `uptime_kuma_api/auth_method.py` | +3/-0 | BEARER |
| `uptime_kuma_api/docstrings.py` | +254/-12 | regenerated notification block + new monitor params |
| `uptime_kuma_api/event.py` | +5/-0 | 3 new event names |
| `uptime_kuma_api/monitor_type.py` | +56/-19 | regenerated |
| `uptime_kuma_api/notification_providers.py` | +430/-13 | regenerated |
| `tests/uptime_kuma_test_case.py` | +40/-8 | env vars, retry, token refresh, timeout bump |
| `tests/test_2fa.py` | +0/-2 | msg-text strip |
| `tests/test_api_key.py` | +0/-4 | msg-text strip |
| `tests/test_docker_host.py` | +0/-3 | msg-text strip |
| `tests/test_login.py` | +0/-2 | msg-text strip |
| `tests/test_maintenance.py` | +0/-10 | msg-text strip |
| `tests/test_monitor.py` | +9/-7 | msg-text strip + beat-wait loop |
| `tests/test_monitor_tag.py` | +0/-2 | msg-text strip |
| `tests/test_notification.py` | +20/-9 | msg-text strip + telegram fields + applyExisting + accept TimeoutError |
| `tests/test_proxy.py` | +0/-3 | msg-text strip |
| `tests/test_settings.py` | +5/-4 | msg-text strip + skip uploadBackup on 2.x |
| `tests/test_status_page.py` | +2/-2 | msg-text strip + analyticsId rename |
| `tests/test_tag.py` | +0/-2 | msg-text strip |

---

## 8. Verification

Live-server run against a `npm run dev` Uptime Kuma 2.4.0 on
`127.0.0.1:3001`:

- **Smoke** (`/tmp/smoke_2x.py`, custom): **40/40 pass.** Covers every new
  monitor type, bearer auth, conditions, remote browser CRUD, cloudflared
  join/leave, full incident lifecycle, chart, paged heartbeats, Webpush
  notification, plus the 4 misc 2.x methods.

- **Full unittest discover**:

  ```
  Ran 66 tests in 55.019s
  OK (skipped=1)
  ```

  The one skip is `test_upload_backup` (uploadBackup removed in 2.x).

Reproduce with:

```sh
python3 -m venv venv
venv/bin/pip install -r requirements.txt -r dev-requirements.txt websocket-client
UPTIME_KUMA_PASSWORD=<your-pw> PYTHONPATH=. venv/bin/python -m unittest discover -s tests
```
