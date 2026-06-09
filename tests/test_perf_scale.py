import os
import time
import unittest

import requests

from uptime_kuma_api import MonitorType, MonitorStatus
from uptime_kuma_test_case import UptimeKumaTestCase


class TestPerfScale(UptimeKumaTestCase):
    """Guards against the deepcopy+poll regression in `wait_for_event`.

    Before the threading.Event signaling refactor, every mutating call did a
    deepcopy of the entire MONITOR_LIST plus full dict-equality compares in a
    10ms poll loop, both O(monitors). `add_monitor` got linearly slower as
    monitor count grew. After the fix the call should be effectively constant
    (network-bound), independent of how many monitors already exist.
    """

    MONITORS = 50  # high enough to expose the old regression, low enough for CI

    def test_add_monitor_is_constant_time(self):
        timings = []
        ids = []
        try:
            for i in range(self.MONITORS):
                t0 = time.perf_counter()
                r = self.api.add_monitor(
                    type=MonitorType.HTTP,
                    name=f"perf-scale-{i}",
                    url="http://127.0.0.1",
                )
                timings.append(time.perf_counter() - t0)
                ids.append(r["monitorID"])
        finally:
            for monitor_id in ids:
                try:
                    self.api.delete_monitor(monitor_id)
                except Exception:
                    pass

        baseline = sum(timings[:5]) / 5
        tail = sum(timings[-5:]) / 5
        # the old code grew roughly linearly with N; even at N=50 the tail
        # ran ~5–10x the head. Allow 3x for jitter; a real regression will
        # blow well past this.
        self.assertLess(
            tail,
            max(baseline * 3.0, 0.5),
            f"add_monitor scaled with monitor count: "
            f"first-5 avg={baseline * 1000:.0f}ms, last-5 avg={tail * 1000:.0f}ms",
        )

    @unittest.skipUnless(
        os.environ.get("UPTIME_KUMA_PERF"),
        "set UPTIME_KUMA_PERF=1 to run the 500-monitor push scale test",
    )
    def test_push_monitors_at_scale(self):
        """Create 500 push monitors and push each one to UP.

        Opt-in (UPTIME_KUMA_PERF=1) because the wall clock is dominated by
        500 socket round-trips for creation plus 500 HTTP pushes — roughly a
        minute against a local dev server. The test exists to exercise the
        wrapper under realistic large-fleet conditions and to surface any
        regression in the threading.Event signaling path that the smaller
        ``test_add_monitor_is_constant_time`` test could miss.
        """
        count = int(os.environ.get("UPTIME_KUMA_PERF_COUNT", "500"))
        push_base = self.api.url.rstrip("/") + "/api/push/"

        ids = []
        create_timings = []
        push_timings = []

        try:
            # phase 1: create the monitors. pushToken is generated server-side
            # (the wrapper auto-fills one in _convert_monitor_input) and lands
            # in the cached monitor record via updateMonitorIntoList.
            for i in range(count):
                t0 = time.perf_counter()
                r = self.api.add_monitor(
                    type=MonitorType.PUSH,
                    name=f"perf-push-{i}",
                )
                create_timings.append(time.perf_counter() - t0)
                ids.append(r["monitorID"])

            # phase 2: read tokens out of the cache in one shot (no per-monitor
            # RPC), then push each one to UP via HTTP.
            id_set = set(ids)
            token_by_id = {
                m["id"]: m["pushToken"]
                for m in self.api.get_monitors()
                if m["id"] in id_set
            }
            self.assertEqual(len(token_by_id), count, "wrapper cache missing some monitors")

            with requests.Session() as s:
                for monitor_id in ids:
                    token = token_by_id[monitor_id]
                    t0 = time.perf_counter()
                    resp = s.get(push_base + token, params={"status": "up", "msg": "OK", "ping": "1"}, timeout=10)
                    push_timings.append(time.perf_counter() - t0)
                    resp.raise_for_status()

            # sanity: a sampled monitor reports UP via the wrapper. heartbeats
            # arrive asynchronously, so poll briefly.
            sample_id = ids[-1]
            deadline = time.time() + 5
            sample_status = None
            while time.time() < deadline:
                beats = self.api.get_monitor_beats(sample_id, 1)
                if beats:
                    sample_status = beats[-1]["status"]
                    if sample_status == MonitorStatus.UP:
                        break
                time.sleep(0.1)
            self.assertEqual(sample_status, MonitorStatus.UP, "sampled push monitor did not reach UP state")
        finally:
            for monitor_id in ids:
                try:
                    self.api.delete_monitor(monitor_id)
                except Exception:
                    pass

        # report — and guard against regressions of the old O(monitors) wait
        create_head = sum(create_timings[:10]) / 10
        create_tail = sum(create_timings[-10:]) / 10
        push_total = sum(push_timings)
        print(
            f"\n[perf] created {count} push monitors: "
            f"head-10 avg={create_head * 1000:.0f}ms, tail-10 avg={create_tail * 1000:.0f}ms "
            f"(total {sum(create_timings):.1f}s); "
            f"pushed {count} heartbeats in {push_total:.1f}s "
            f"(avg {push_total / count * 1000:.0f}ms each)"
        )
        self.assertLess(
            create_tail,
            max(create_head * 5.0, 1.0),
            f"add_monitor scaled badly across {count} push monitors: "
            f"head-10 avg={create_head * 1000:.0f}ms, tail-10 avg={create_tail * 1000:.0f}ms",
        )


if __name__ == "__main__":
    unittest.main()
