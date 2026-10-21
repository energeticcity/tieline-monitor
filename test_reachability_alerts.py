import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from reachability_alerts import ReachabilityAlerts


class ReachabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.json'
        self.policy = ReachabilityAlerts(self.path)

    def observe(self, values, start=1000, **kwargs):
        return [self.policy.observe('studio', value, start + i * 30, **kwargs) for i, value in enumerate(values)]

    def test_single_failures_and_recoveries_do_not_text(self):
        self.assertEqual(self.observe([True, False, True, False, True]), [None] * 5)

    def test_confirmed_outage_and_recovery_once_each(self):
        self.assertEqual(self.observe([True, False, False, False, False, True, True, True]),
                         [None, None, None, 'offline', None, None, 'recovered', None])

    def test_samples_alone_do_not_prove_elapsed_time(self):
        self.policy.observe('studio', True, 1000)
        self.assertIsNone(self.policy.observe('studio', False, 1001))
        self.assertIsNone(self.policy.observe('studio', False, 1002))
        self.assertIsNone(self.policy.observe('studio', False, 1003))
        self.assertEqual(self.policy.observe('studio', False, 1061), 'offline')

    def test_first_run_down_is_confirmed_instead_of_silently_ignored(self):
        self.assertEqual(self.observe([False, False, False]), [None, None, 'offline'])

    def test_continuously_down_does_not_repeat_even_after_cooldown(self):
        self.observe([False, False, False])
        self.assertIsNone(self.policy.observe('studio', False, 5000))
        self.assertIsNone(self.policy.observe('studio', False, 10000))

    def test_flapping_is_limited_and_suppressed_outage_has_no_orphan_recovery(self):
        self.observe([False, False, False, True, True])
        self.assertEqual(self.observe([False, False, False, True, True], 1150), [None] * 5)
        self.assertEqual(self.observe([False, False, False], 2000), [None, None, 'offline'])

    def test_persistent_outage_delayed_by_cooldown_eventually_alerts(self):
        self.observe([False, False, False, True, True])
        self.assertEqual(self.observe([False, False, False], 1150), [None] * 3)
        self.assertIsNone(self.policy.observe('studio', False, 1959))
        self.assertEqual(self.policy.observe('studio', False, 1960), 'offline')

    def test_devices_have_independent_confirmation_and_cooldown(self):
        self.observe([False, False, False])
        self.assertIsNone(self.policy.observe('transmitter', False, 1060))
        self.assertIsNone(self.policy.observe('transmitter', False, 1090))
        self.assertEqual(self.policy.observe('transmitter', False, 1120), 'offline')

    def test_restart_retains_outage_notice_and_cooldown_but_reconfirms_recovery(self):
        self.observe([False, False, False])
        self.policy = ReachabilityAlerts(self.path)
        self.assertIsNone(self.policy.observe('studio', False, 1090))
        self.assertIsNone(self.policy.observe('studio', True, 1120))
        self.assertEqual(self.policy.observe('studio', True, 1150), 'recovered')
        self.policy = ReachabilityAlerts(self.path)
        self.assertEqual(self.observe([False, False, False], 1180), [None] * 3)

    def test_unknown_or_stale_observations_break_confirmation(self):
        self.assertEqual(self.observe([False, False, None, False, False]), [None] * 5)
        self.assertIsNone(self.policy.observe('studio', False, 2000))
        self.assertIsNone(self.policy.observe('studio', False, 2030))
        self.assertEqual(self.policy.observe('studio', False, 2060), 'offline')

    def test_longer_configured_poll_interval_can_still_confirm_outage(self):
        self.assertIsNone(self.policy.observe('studio', False, 1000, maximum_sample_gap=660))
        self.assertIsNone(self.policy.observe('studio', False, 1300, maximum_sample_gap=660))
        self.assertEqual(self.policy.observe('studio', False, 1600, maximum_sample_gap=660), 'offline')

    def test_paused_sms_does_not_reserve_an_unsent_outage_notice(self):
        self.assertEqual(self.observe([False, False, False], notifications_enabled=False), [None] * 3)
        self.assertFalse(json.loads(self.path.read_text())['devices']['studio']['down_notice'])
        self.assertEqual(self.policy.observe('studio', False, 1090), 'offline')

    def test_corrupt_state_is_preserved_and_cannot_restart_a_flood(self):
        self.path.write_text('not json')
        self.policy = ReachabilityAlerts(self.path)
        with self.assertRaisesRegex(RuntimeError, 'cannot be read'):
            self.policy.observe('studio', False, 1000)
        self.assertEqual(self.path.read_text(), 'not json')

    def test_failed_atomic_save_stops_repeated_notification_attempts(self):
        with patch.object(self.policy, '_save', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.policy.observe('studio', True, 1000)
        with self.assertRaisesRegex(RuntimeError, 'cannot be saved'):
            self.policy.observe('studio', False, 1030)

    def test_backwards_clock_cannot_bypass_cooldown(self):
        self.observe([False, False, False, True, True])
        self.assertEqual(self.observe([False, False, False], 900), [None] * 3)

    def test_invalid_input_cannot_become_reachability(self):
        for value in ['false', 0, [], {}]:
            with self.assertRaises(ValueError):
                self.policy.observe('studio', value, 1000)
        for now in [float('nan'), float('inf'), -1, True]:
            with self.assertRaises(ValueError):
                self.policy.observe('studio', True, now)


if __name__ == '__main__':
    unittest.main()
