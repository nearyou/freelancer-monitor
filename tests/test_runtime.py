from unittest import TestCase, mock

from freelancer_monitor.runtime import run_poll_loop


class PollScheduleTests(TestCase):
    def run_schedule(self, durations, *, fails=False):
        clock = [0.0]
        starts = []
        waits = []
        stop = mock.Mock()
        stop.is_set.side_effect = lambda: len(starts) >= len(durations)

        def poll():
            index = len(starts)
            starts.append(clock[0])
            clock[0] += durations[index]
            if fails:
                raise OSError("API unavailable")
            return 1, 1

        def wait(seconds):
            waits.append(seconds)
            clock[0] += seconds

        stop.wait.side_effect = wait
        monitor = mock.Mock()
        monitor.run_once.side_effect = poll
        with mock.patch("freelancer_monitor.runtime.time.monotonic", side_effect=lambda: clock[0]):
            run_poll_loop(monitor, 15, stop)
        return starts, waits

    def test_poll_starts_are_fifteen_seconds_apart(self):
        starts, waits = self.run_schedule([2, 3, 1])
        self.assertEqual(starts, [0, 15, 30])
        self.assertEqual(waits, [13, 12, 14])

    def test_slow_poll_skips_missed_ticks_without_bursting(self):
        starts, waits = self.run_schedule([40, 2])
        self.assertEqual(starts, [0, 55])
        self.assertEqual(waits, [15, 13])

    def test_poll_failure_keeps_schedule_running(self):
        with self.assertLogs("freelancer_monitor.runtime", level="ERROR"):
            starts, _ = self.run_schedule([1, 1], fails=True)
        self.assertEqual(starts, [0, 15])
