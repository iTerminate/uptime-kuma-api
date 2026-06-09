# Wrapper performance — scaling to 1k+ monitors

This document records the performance changes made to `uptime_kuma_api` on
branch `performance-improvement`, on top of the 2.4.0 2.x-support work
(see `2x-upgrade-report.md`).

`git diff --stat master`:

```
 uptime_kuma_api/api.py  | 278 +++++++++++++++++++++++++++--------- (+185/-93)
 tests/test_perf_scale.py | +93  (new)
```

---

## 1. Symptom

Mutating calls — `add_monitor`, `edit_monitor`, `delete_monitor`,
`add_monitor_tag`, `delete_monitor_tag`, and the other ~20 sites that wrap
their server call in `with self.wait_for_event(...)` — got noticeably slower
the more monitors the server already had. The user-visible effect was
"adding another monitor takes longer the more monitors I already have." The
cost grew roughly linearly with monitor count and was dominated by the
wrapper, not the server.

## 2. Root cause

Every mutating call funnelled through `wait_for_event` (old `api.py:537-557`).
The implementation was snapshot-and-poll:

```python
snapshot = deepcopy(self._event_data.get(event))      # full deepcopy of MONITOR_LIST
try:
    yield
...
while self._event_data[event] is None or self._event_data[event] == snapshot:
    if time.time() - timestamp > self.timeout:
        raise Timeout(...)
    time.sleep(0.01)                                  # each poll: full dict equality compare
```

Two costs, both `O(monitors × fields-per-monitor)`:

1. **Upfront `copy.deepcopy`** of the entire `MONITOR_LIST` dict. Each
   monitor carries ~50 fields, several nested (`tags`, `notificationIDList`,
   `conditions`, `accepted_statuscodes`, `oauth_*`, TLS bundles — see
   upstream `server/model/monitor.js:117-256`).
2. **Per-poll Python dict equality compare** between cache and snapshot,
   recursing through every value. With a 10 ms poll interval and a typical
   50–100 ms server round-trip, every mutation paid 5–10 of these.

Secondary cost in the heartbeat handler (`api.py:652` pre-fix):

```python
self._event_data[Event.IMPORTANT_HEARTBEAT_LIST][monitor_id] = (
    [data] + self._event_data[Event.IMPORTANT_HEARTBEAT_LIST][monitor_id]
)
```

`[data] + list` is `O(len(list))` per prepend, and the list was
**unbounded** (the regular `HEARTBEAT_LIST` capped at 150 via
`list.pop(0)`, itself `O(n)`; `IMPORTANT_HEARTBEAT_LIST` had no cap at
all). At 1k monitors generating background heartbeats this was a constant
CPU drag that competed with the main thread.

Tertiary: `_event_update_monitor_into_list` did `dict.update(data)` with
whatever key type the server emitted, while `add_monitor_tag` /
`delete_monitor_tag` wrote `[str(monitor_id)]`. The cache could end up
with mixed int/str keys; `_event_delete_monitor_from_list` had a tolerant
workaround that tried both.

## 3. Changes

Four changes, all in `uptime_kuma_api/api.py`.

### 3.1 `wait_for_event` → `threading.Event` signalling

One `threading.Event` per slot, allocated once in `__init__`. Each handler
calls `.set()` after writing its slot. `wait_for_event` clears the event
**before** the `yield` (so any signal that arrives during `_call` is
captured) and waits on it after.

```python
@contextmanager
def wait_for_event(self, event: Event) -> None:
    if event == Event.AUTO_LOGIN and self._event_data.get(event) is not None:
        yield                                    # one-shot, already fired
        return

    signal = self._event_signals[event]
    signal.clear()
    try:
        yield
    except:
        raise
    else:
        if not signal.wait(self.timeout):
            raise Timeout(...)
```

Two extra rules:

- **`MONITOR_LIST` also fires on deltas.** `_event_update_monitor_into_list`
  and `_event_delete_monitor_from_list` `.set()` the `MONITOR_LIST` signal
  too — on 2.x they are the only signals a CRUD op produces (the server no
  longer emits a full `monitorList` on add/edit/delete; see upstream
  `server/server.js:775,1071,1186`).
- **`AUTO_LOGIN` short-circuits.** The handler may set the slot during
  connect, before the caller ever enters `with wait_for_event(...)`. If
  it's already non-None, return immediately rather than waiting for an
  event that already happened.

### 3.2 `threading.RLock` around `_event_data` access

A single global `RLock` (`self._event_lock`). Held inside every handler
write and around the snapshot in `_get_event_data`. With the polling loop
removed, readers can otherwise iterate a dict the socketio receive thread
is mutating (e.g. `_event_update_monitor_into_list`'s `dict.update`), which
raises `RuntimeError: dictionary changed size during iteration`.

One global `RLock` is simpler than per-slot locks and contention is
negligible — handlers are short and reads are rare relative to writes.

### 3.3 `collections.deque` for heartbeat lists

```python
HEARTBEAT_LIST[monitor_id]            → deque(maxlen=150)
IMPORTANT_HEARTBEAT_LIST[monitor_id]  → deque(maxlen=500)
```

- `maxlen=150` matches the pre-existing wrapper cap and turns `O(n)`
  `list.pop(0)` into `O(1)` `popleft`.
- `maxlen=500` matches the **upstream server** cap (see
  `server/client.js:74-97`) and turns the `O(n)` `[data] + list` prepend
  into `O(1)` `appendleft`. Also caps the previously unbounded growth.

`_get_event_data` converts deques to plain `list()` at the boundary so
external callers see the same shape they always have. The read-side
`deepcopy` is preserved (see §5 for why).

### 3.4 Monitor-id key normalisation

All monitor-id keys in `MONITOR_LIST` are coerced to `int` on entry:

- `_event_monitor_list` — `data = {int(k): v for k, v in data.items()}`
- `_event_update_monitor_into_list` — same coercion before `.update()`
- `add_monitor_tag` / `delete_monitor_tag` cache writes — `int(monitor_id)`
  instead of `str(monitor_id)`

`_event_delete_monitor_from_list` keeps a tolerant fallback (tries int,
raw, and str) for any pre-existing entry that slipped through.

### 3.5 Manual cache mutations now also `.set()` the signal

Three status-page methods bypass the server's event stream entirely (2.x
doesn't emit `statusPageList` on add/edit/delete) and instead mutate the
cache directly:

- `add_status_page` (after `getStatusPage` refresh)
- `delete_status_page` (after manual del)
- `save_status_page` (after `getStatusPage` refresh)

The old polling loop happened to notice these mutations because it compared
the cache against a snapshot every 10 ms. The new signal-based wait
doesn't — the mutating method must `.set()` the signal explicitly. All
three are now wrapped in `with self._event_lock:` and call
`self._event_signals[Event.STATUS_PAGE_LIST].set()` after the mutation.

Surfaced during `unittest discover` — two `test_status_page` cases timed
out until this was added.

## 4. New test — `tests/test_perf_scale.py`

Two tests:

### 4.1 `test_add_monitor_is_constant_time` (always runs)

Adds 50 monitors in a loop, records wall-clock for each `add_monitor`
call, then deletes them. Asserts the tail-5 average is no more than 3× the
head-5 average. 50 is enough to expose the deepcopy regression without
slowing CI meaningfully (~12 s against the local dev server).

### 4.2 `test_push_monitors_at_scale` (opt-in)

Gated behind `UPTIME_KUMA_PERF=1`; count configurable via
`UPTIME_KUMA_PERF_COUNT` (default 500).

- Creates N `MonitorType.PUSH` monitors.
- Reads each `pushToken` from the wrapper cache in a single
  `get_monitors()` call (no per-monitor RPC).
- HTTP-pushes `status=up` to `/api/push/<token>` for each monitor.
- Polls `get_monitor_beats` on one sample monitor and asserts it reaches
  `MonitorStatus.UP`.
- Asserts head-10 vs tail-10 creation timing stays within 5× (regression
  guard) and prints the per-phase totals.

Run with:

```sh
UPTIME_KUMA_PASSWORD=<pw> UPTIME_KUMA_PERF=1 \
  PYTHONPATH=. venv/bin/python -m unittest discover -s tests -p test_perf_scale.py -v
```

## 5. Decisions deliberately not made

- **The read-side `deepcopy` in `_get_event_data` stays.** The wrapper's
  own `edit_monitor` (`api.py:1761-1762`) does
  `data = self.get_monitor(id_); data.update(kwargs)` — if `data` were a
  live reference into the cache, that `.update` would silently corrupt
  cached state before sending to the server. External callers likely do
  the same. Replacing `copy.deepcopy` with a `json.loads(json.dumps(...))`
  round-trip (typically 5–10× faster for pure-JSON payloads) is a future
  option once profiling shows reads as the next hot spot.
- **No cache-resync mechanism.** The wrapper still relies on the server's
  event stream as the sole source of truth — if a delta is dropped (socket
  blip, server bug), the cache silently diverges. Both the old
  snapshot-and-poll and the new signal-based wait behave identically on
  missed events: `wait_for_event` raises `Timeout` if the signal never
  arrives. A real fix (opt-in `force_refresh=True` on `get_monitors`, or
  periodic resync on socket reconnect) belongs in its own PR.
- **Existence pre-checks in `delete_*` methods left alone.**
  `delete_monitor_tag` / `delete_monitor` / `delete_notification` /
  `delete_proxy` / `delete_api_key` / `delete_status_page` all iterate a
  `get_*()` call before sending the delete. Validation sugar, not perf.
- **`get_monitor(id)` not converted to an O(1) cache lookup.** Becomes
  trivial after the key normalisation but ship separately so it's
  bisectable.
- **Upstream server changes.** Wrapper-only, per the original ask.

## 6. Measured impact

Numbers against a `npm run dev` Uptime Kuma 2.4.0 on `127.0.0.1:3001`,
fresh database.

### `add_monitor` scaling — 500 push monitors

```
[perf] created 500 push monitors:
  head-10 avg=168ms, tail-10 avg=168ms (total 84.0s);
  pushed 500 heartbeats in 1.6s (avg 3ms each)
```

- **Tail / head ratio: 1.0×** — perfectly flat. Wrapper-side overhead is
  effectively constant.
- Per-call cost (~168 ms) is dominated by the server's socket round-trip
  for `add`, not by the wrapper.
- Push path is HTTP, not socket.io, so it doesn't exercise the
  signalling change directly — it's there to validate end-to-end
  behaviour and to give a realistic large-fleet baseline.

### Estimated savings vs. pre-fix

Back-of-envelope, not benchmarked against the old code:

| Monitors | Old wrapper overhead per call | New | Speedup |
|---|---|---|---|
| 100 | 30–100 ms | ~constant | 1–2× |
| 1 000 | 300 ms – 1.5 s | ~constant | 5–30× |
| 10 000 | 3–15 s | ~constant | 50–300× |

Cost was linear in monitor count; now it's flat. The bigger the
deployment, the bigger the win.

### Existing test suite

```
Ran 68 tests in 76.069s — OK (skipped=2)
```

The two skips are `test_upload_backup` (pre-existing — `uploadBackup` was
removed upstream in 2.x) and `test_push_monitors_at_scale` (opt-in via
`UPTIME_KUMA_PERF=1`).

## 7. Files modified

| File | ± | What |
|---|---|---|
| `uptime_kuma_api/api.py` | +185 / −93 | imports (`threading`, `deque`); `_event_signals` + `_event_lock` in `__init__`; `wait_for_event` rewritten; `_get_event_data` lock + deque-to-list at boundary; every event handler now uses lock + `.set()`; deque for heartbeat lists; int-key normalisation; `add/delete/save_status_page` lock + signal |
| `tests/test_perf_scale.py` | new, 93 lines | `test_add_monitor_is_constant_time` (always-on, N=50) and `test_push_monitors_at_scale` (opt-in, N=500 default) |
