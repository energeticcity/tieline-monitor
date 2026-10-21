import asyncio
import importlib.util
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


class StopLoop(BaseException):
    pass


class MonitorAlerts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        flask = types.ModuleType('flask')
        app = MagicMock()
        app.route.side_effect = lambda *a, **k: lambda f: f
        flask.Flask = lambda *a, **k: app
        for name in ['render_template', 'request', 'redirect', 'url_for', 'session', 'flash', 'jsonify']:
            setattr(flask, name, MagicMock())
        waitress = types.ModuleType('waitress')
        waitress.serve = MagicMock()
        with patch.dict('sys.modules', {'flask': flask, 'waitress': waitress}):
            spec = importlib.util.spec_from_file_location('synthetic_monitor', Path(__file__).with_name('monitor.py'))
            self.monitor = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.monitor)
        self.monitor.BASE_DIR = Path(self.tmp.name)

    async def simulate(self, studio, sources=None):
        sources = sources or ['normal'] * len(studio)
        tick = 0
        settings = dict(self.monitor.DEFAULT_SETTINGS, alert_numbers=['synthetic-recipient'])
        async def get(host, oids, community):
            if host == settings['studio_ip']:
                return {'cxn_state': '1' if studio[tick] else None}
            source = sources[tick]
            if source == 'unknown':
                return {k: None for k in self.monitor.TX_OIDS}
            return {'src0_state': '1' if source == 'normal' else '0',
                    'src1_state': '1' if source == 'backup' else '0',
                    'src2_state': '0', 'cxn_state': '1'}
        async def walk(*args):
            return []
        async def sleep(*args):
            nonlocal tick
            tick += 1
            if tick == len(studio):
                raise StopLoop()
        with patch.object(self.monitor, 'load_settings', return_value=settings), \
             patch.object(self.monitor, 'snmp_get', side_effect=get), \
             patch.object(self.monitor, 'snmp_walk_alarms', side_effect=walk), \
             patch.object(self.monitor, 'send_sms', return_value=['synthetic-recipient']) as send, \
             patch.object(self.monitor.time, 'time', side_effect=lambda: 1700000000 + tick * 30), \
             patch.object(self.monitor.asyncio, 'sleep', side_effect=sleep):
            with self.assertRaises(StopLoop):
                await self.monitor._monitor_loop()
            return [call.args[0] for call in send.call_args_list]

    async def test_brief_studio_failures_do_not_text(self):
        self.assertEqual(await self.simulate([True, False, True, False, True]), [])

    async def test_confirmed_studio_outage_and_recovery_text_once(self):
        messages = await self.simulate([True, False, False, False, True, True])
        self.assertEqual(len(messages), 2)
        self.assertIn('management checks failed for at least 60 seconds', messages[0])
        self.assertIn('back online (confirmed)', messages[1])

    async def test_actual_audio_loss_bypasses_reachability_confirmation(self):
        messages = await self.simulate([True, False], ['normal', 'loss'])
        self.assertEqual(len(messages), 1)
        self.assertIn('No audio source active', messages[0])

    async def test_backup_audio_switch_remains_immediate(self):
        messages = await self.simulate([True, False], ['normal', 'backup'])
        self.assertEqual(len(messages), 1)
        self.assertIn('switched to PlayoutONE', messages[0])

    async def test_missing_snmp_is_unknown_not_false_audio_loss_or_recovery(self):
        self.assertEqual(await self.simulate([True, True, True], ['normal', 'unknown', 'normal']), [])

    async def test_corrupt_reachability_state_does_not_disable_audio_alarms(self):
        (self.monitor.BASE_DIR / 'reachability-alert-state.json').write_text('bad state')
        messages = await self.simulate([True, True], ['normal', 'loss'])
        self.assertEqual(len(messages), 1)
        self.assertIn('No audio source active', messages[0])

    async def test_snmp_no_such_instance_is_not_a_reachable_numeric_status(self):
        snmp = types.ModuleType('pysnmp.hlapi.v3arch.asyncio')
        snmp.SnmpEngine = MagicMock()
        snmp.CommunityData = MagicMock()
        snmp.UdpTransportTarget = types.SimpleNamespace(create=AsyncMock(return_value=object()))
        snmp.ContextData = MagicMock()
        snmp.ObjectType = MagicMock()
        snmp.ObjectIdentity = MagicMock()
        snmp.get_cmd = AsyncMock(return_value=(None, 0, None, [('synthetic-oid', 'No Such Instance currently exists')]))
        with patch.dict('sys.modules', {'pysnmp.hlapi.v3arch.asyncio': snmp}):
            self.assertEqual(await self.monitor._snmp_get_pysnmp('synthetic-host', {'cxn_state': 'synthetic-oid'}, 'synthetic-community'), {'cxn_state': None})
        snmp.get_cmd.return_value = (None, 0, None, [('synthetic-oid', '2')])
        with patch.dict('sys.modules', {'pysnmp.hlapi.v3arch.asyncio': snmp}):
            self.assertEqual(await self.monitor._snmp_get_pysnmp('synthetic-host', {'cxn_state': 'synthetic-oid'}, 'synthetic-community'), {'cxn_state': '2'})

    async def test_snmp_failure_logs_kind_without_private_exception_text(self):
        snmp = types.ModuleType('pysnmp.hlapi.v3arch.asyncio')
        snmp.SnmpEngine = MagicMock()
        snmp.CommunityData = MagicMock()
        snmp.UdpTransportTarget = types.SimpleNamespace(create=AsyncMock(return_value=object()))
        snmp.ContextData = MagicMock()
        snmp.ObjectType = MagicMock()
        snmp.ObjectIdentity = MagicMock()
        snmp.get_cmd = AsyncMock(side_effect=RuntimeError('private-provider-error'))
        with patch.dict('sys.modules', {'pysnmp.hlapi.v3arch.asyncio': snmp}), patch.object(self.monitor.log, 'warning') as warning:
            self.assertEqual(await self.monitor._snmp_get_pysnmp('synthetic-host', {'cxn_state': 'synthetic-oid'}, 'private-community'), {'cxn_state': None})
            self.assertNotIn('private-', str(warning.call_args_list))

    def test_incomplete_or_invalid_source_values_do_not_assert_silence(self):
        for data in [{}, {'src0_state': 'No Such Instance'}, {'src0_state': '0'}]:
            self.assertEqual(self.monitor.detect_audio_state(data), 'unknown')
        self.assertEqual(self.monitor.detect_audio_state({'src0_state': '0', 'src1_state': '0', 'src2_state': '0'}), 'loss')


if __name__ == '__main__':
    unittest.main()
