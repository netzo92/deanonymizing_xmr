"""Synthetic prospective timing and failure checks; never opens analysis data."""
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from monero_rpc import RPCError
from pool_observer import APPLICATION_ID, PoolObserver, PoolRPC, main, parse_args, parse_pool


def identity(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def transaction(value, fee=100, weight=200, receive_time=0):
    return {'id_hash': identity(value), 'fee': fee, 'weight': weight,
            'receive_time': receive_time, 'tx_blob': 'DO_NOT_STORE_BLOB',
            'tx_json': 'DO_NOT_STORE_JSON', 'peer_ip': 'DO_NOT_STORE_PEER'}


class FakeClock:
    def __init__(self):
        self.utc, self.mono = 1800000000.0, 1000.0

    def time(self): return self.utc
    def monotonic(self): return self.mono

    def advance(self, seconds):
        self.utc += seconds
        self.mono += seconds


class FakeRPC:
    def __init__(self):
        self.tip = 100
        self.pool = []
        self.pool_response = None
        self.info_changes = {}
        self.blocks = {}
        self.fail_height = None
        self.changed_height = None
        self.requested = []

    def begin_cycle(self): pass

    def get_info(self):
        return dict({'status': 'OK', 'height': self.tip + 1,
                     'top_block_hash': identity(f'block-{self.tip}'), 'mainnet': True,
                     'nettype': 'mainnet', 'testnet': False, 'stagenet': False,
                     'version': '', 'restricted': True, 'untrusted': False,
                     'synchronized': True, 'busy_syncing': False,
                     'height_without_bootstrap': self.tip + 1, 'bootstrap_daemon_address': '',
                     'tx_pool_size': len(self.pool), 'start_time': 0}, **self.info_changes)

    def get_pool(self):
        if isinstance(self.pool_response, Exception): raise self.pool_response
        if self.pool_response is not None: return self.pool_response
        return {'status': 'OK', 'transactions': copy.deepcopy(self.pool), 'untrusted': False,
                'spent_key_images': ['DO_NOT_STORE_KEY_IMAGES']}

    def get_block_header(self, height):
        return {'height': height, 'hash': identity(f'changed-{height}' if height == self.changed_height else f'block-{height}'),
                'prev_hash': identity(f'block-{height - 1}'), 'timestamp': 1799000000 + height * 120,
                'num_txes': len(self.blocks.get(height, []))}

    def get_block(self, height):
        self.requested.append(height)
        if height == self.fail_height: raise RPCError('Synthetic unavailable block')
        return {'block_header': self.get_block_header(height), 'tx_hashes': self.blocks.get(height, [])}


class PoolObserverTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.args = parse_args(['--db', str(self.root / 'private/pool.db'),
                               '--output-dir', str(self.root / 'public'),
                               '--node', 'https://user:secret@example.invalid:18081/private?token=secret',
                               '--initial-blocks', '2', '--blocks-per-cycle', '2', '--min-free-mb', '1'])
        self.rpc, self.clock = FakeRPC(), FakeClock()

    def observer(self): return PoolObserver(self.args, self.rpc, self.clock)

    def rows(self, query, parameters=()):
        with closing(sqlite3.connect(self.args.db)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query, parameters)]

    def test_disappearance_is_not_confirmation_and_delay_requires_prospective_membership(self):
        observer = self.observer()
        initial = observer.cycle()
        self.assertEqual(initial['state'], 'warming_up')
        self.assertEqual(self.rpc.requested, [97, 98])
        self.clock.advance(60)
        self.rpc.pool = [transaction('A')]
        found = observer.cycle()
        self.assertEqual(found['outcomes']['pending'], 1)
        self.assertEqual(found['outcomes']['cold_start_transactions'], 0)
        self.clock.advance(60)
        self.rpc.pool = []
        self.rpc.tip = 101
        absent = observer.cycle()
        self.assertEqual(absent['outcomes']['disappeared'], 1)
        self.assertEqual(absent['outcomes']['confirmed'], 0)
        self.clock.advance(60)
        self.rpc.tip = 103
        self.rpc.blocks[101] = [identity('A'), identity('never-in-pool')]
        result = observer.cycle()
        outcomes = result['outcomes']
        self.assertEqual(outcomes['confirmed'], 1)
        self.assertEqual(outcomes['confirmed_block_transactions'], 2)
        self.assertEqual(outcomes['observed_confirmations'], 1)
        self.assertEqual(outcomes['confirmations_seen_before_block'], 1)
        self.assertEqual(outcomes['confirmation_delay']['sample_count'], 1)
        self.assertEqual(outcomes['confirmation_delay']['median_seconds'], 120)
        self.assertEqual(sum(item['count'] for item in outcomes['confirmation_delay']['bins']), 1)
        observer.cycle()
        self.assertEqual(len(self.rows('SELECT * FROM blocks')), 5)
        self.assertEqual(self.rows('SELECT delay_seconds FROM transactions')[0]['delay_seconds'], 120)

    def test_initial_pool_is_cold_and_already_existing_block_is_not_prospective(self):
        self.rpc.pool = [transaction('A')]
        self.rpc.blocks[98] = [identity('A')]
        result = self.observer().cycle()
        self.assertEqual(result['outcomes']['confirmed'], 1)
        self.assertEqual(result['outcomes']['cold_start_transactions'], 1)
        self.assertEqual(result['outcomes']['confirmations_seen_before_block'], 0)
        self.assertEqual(result['outcomes']['confirmation_delay']['sample_count'], 0)
        self.assertEqual(result['outcomes']['confirmation_delay']['excluded_count'], 1)
        self.assertTrue(result['coverage']['initial_window_left_truncated'])

    def test_mutable_receive_time_exact_integers_and_public_whitelist(self):
        huge = 2**64 - 1
        self.rpc.pool = [transaction('A', huge, huge)]
        observer = self.observer()
        first = observer.cycle()
        self.assertEqual(first['current']['fee_atomic_total'], str(huge))
        self.assertEqual(first['current']['receive_time_unknown'], 1)
        self.assertIsNone(first['source']['node_version'])
        for receipt in [1799999000, 1799999050]:
            self.clock.advance(60)
            self.rpc.pool[0]['receive_time'] = receipt
            result = observer.cycle()
        raw = self.rows('SELECT node_receive_time,fee,weight FROM observations ORDER BY id')
        self.assertEqual([row['node_receive_time'] for row in raw], [None, 1799999000, 1799999050])
        self.assertTrue(all(row['fee'] == str(huge) and row['weight'] == str(huge) for row in raw))
        encoded = observer.path.read_text()
        for private in [identity('A'), '1799999000', '1799999050', 'DO_NOT_STORE', 'secret', 'node_receive_time']:
            self.assertNotIn(private, encoded)
        self.assertNotIn(b'DO_NOT_STORE', Path(self.args.db).read_bytes())
        self.assertEqual(result['source']['origin'], 'https://example.invalid:18081')
        self.assertEqual(Path(self.args.db).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.rows('PRAGMA application_id')[0]['application_id'], APPLICATION_ID)
        self.assertEqual(self.rows('SELECT length(observer_sha256) AS n FROM sessions')[0]['n'], 64)

    def test_pool_failure_cannot_replace_last_complete_snapshot_or_infer_absence(self):
        self.rpc.pool = [transaction('A')]
        observer = self.observer()
        first = observer.cycle()
        for response in [{'status': 'BUSY'}, {'status': 'OK', 'transactions': None},
                         {'status': 'OK', 'transactions': [transaction('bad', fee=True)]},
                         {'status': 'OK', 'transactions': [transaction('A'), transaction('A')]},
                         RPCError('Synthetic timeout')]:
            with self.subTest(response=type(response).__name__):
                self.rpc.pool_response = response
                self.clock.advance(60)
                failed = observer.cycle()
                self.assertEqual(failed['state'], 'error')
                self.assertEqual(failed['current'], first['current'])
                self.assertEqual(failed['outcomes']['pending'], 1)
                self.assertFalse(failed['coverage']['last_poll_complete'])
        self.assertEqual(len(self.rows('SELECT * FROM observations')), 1)
        self.assertEqual(failed['coverage']['failed_polls'], 5)
        self.assertEqual(failed['outcomes']['followup_censored_transactions'], 1)
        self.rpc.pool_response = None
        self.rpc.pool = [transaction('B')]
        recovered = observer.cycle()
        self.assertEqual(recovered['outcomes']['cold_start_transactions'], 2)
        self.assertEqual(recovered['outcomes']['disappeared'], 1)

    def test_empty_upstream_vector_encoding_and_limits(self):
        self.assertEqual(parse_pool({'status': 'OK'}, 2), [])
        with self.assertRaisesRegex(RPCError, 'limit'):
            parse_pool({'status': 'OK', 'transactions': [transaction('A'), transaction('B')]}, 1)
        for invalid in [-1, 1.5, '100', True, 2**64]:
            with self.subTest(invalid=invalid), self.assertRaises(RPCError):
                parse_pool({'status': 'OK', 'transactions': [transaction('A', fee=invalid)]}, 1)
        self.assertIsNone(parse_pool({'status': 'OK', 'transactions': [transaction('A', receive_time=True)]}, 1)[0]['receive_time'])

    def test_returning_transaction_preserves_first_seen_and_counts_reappearance(self):
        self.rpc.pool = [transaction('A')]
        observer = self.observer()
        observer.cycle()
        first_seen = self.rows('SELECT first_seen FROM transactions')[0]['first_seen']
        observer.cycle()
        self.assertEqual(self.rows('SELECT reappearances FROM transactions')[0]['reappearances'], 0)
        self.rpc.pool = []
        self.clock.advance(60)
        observer.cycle()
        self.rpc.pool = [transaction('A')]
        self.clock.advance(60)
        result = observer.cycle()
        row = self.rows('SELECT first_seen,reappearances FROM transactions')[0]
        self.assertEqual(row, {'first_seen': first_seen, 'reappearances': 1})
        self.assertEqual(result['outcomes']['pending'], 1)

    def test_partial_block_progress_retries_without_duplicate_joins(self):
        self.args.initial_blocks, self.args.blocks_per_cycle = 4, 4
        self.rpc.pool = [transaction('A')]
        self.rpc.blocks[95] = [identity('A')]
        self.rpc.fail_height = 96
        observer = self.observer()
        failed = observer.cycle()
        self.assertEqual(failed['state'], 'error')
        self.assertEqual(failed['coverage']['last_processed_height'], 95)
        self.assertEqual(failed['outcomes']['observed_confirmations'], 1)
        self.rpc.pool = []
        self.rpc.fail_height = None
        good = observer.cycle()
        self.assertEqual(good['coverage']['last_processed_height'], 98)
        self.assertEqual(good['outcomes']['observed_confirmations'], 1)
        self.assertEqual(self.rpc.requested, [95, 96, 96, 97, 98])

    def test_reorg_and_repeated_block_transaction_pause_cursor(self):
        observer = self.observer()
        observer.cycle()
        self.rpc.changed_height = 98
        result = observer.cycle()
        self.assertEqual(result['state'], 'paused')
        self.assertEqual(result['coverage']['last_processed_height'], 98)
        self.rpc.changed_height = None
        self.rpc.tip = 104
        self.rpc.blocks[99] = [identity('A'), identity('A').upper()]
        invalid = observer.cycle()
        self.assertEqual(invalid['state'], 'error')
        self.assertEqual(invalid['coverage']['last_processed_height'], 98)
        self.rpc.blocks[99] = [identity('A')]
        self.rpc.pool = [transaction('A')]
        self.rpc.blocks[100] = [identity('A')]
        duplicate = observer.cycle()
        self.assertEqual(duplicate['state'], 'paused')
        self.assertEqual(duplicate['coverage']['last_processed_height'], 99)
        self.assertEqual(duplicate['outcomes']['observed_confirmations'], 1)
        again = observer.cycle()
        self.assertEqual(again['state'], 'paused')
        self.assertIn('reappeared', again['error']['message'])

    def test_bounded_catchup_records_gap_once_and_censors_followup(self):
        self.args.catchup_window_blocks = 4
        observer = self.observer()
        observer.cycle()
        self.rpc.pool = [transaction('A')]
        self.clock.advance(60)
        observer.cycle()
        self.rpc.pool = []
        self.rpc.tip = 110
        self.rpc.blocks[105] = [identity('A')]
        self.clock.advance(60)
        result = observer.cycle()
        gaps = [gap for gap in result['gaps'] if gap['kind'] == 'confirmation_height_gap']
        self.assertEqual([(gap['from_height'], gap['to_height']) for gap in gaps], [(99, 104)])
        self.assertEqual(result['outcomes']['confirmed'], 1)
        self.assertEqual(result['outcomes']['confirmation_delay']['sample_count'], 0)
        self.assertEqual(result['coverage']['last_processed_height'], 106)
        result = observer.cycle()
        self.assertEqual(result['coverage']['last_processed_height'], 108)
        self.assertEqual(len([gap for gap in result['gaps'] if gap['kind'] == 'confirmation_height_gap']), 1)

    def test_restart_closes_crashed_poll_and_excludes_cross_session_delays(self):
        observer = self.observer()
        observer.cycle()
        self.rpc.pool = [transaction('A')]
        self.clock.advance(60)
        observer.cycle()
        with closing(sqlite3.connect(self.args.db)) as conn, conn:
            conn.execute("INSERT INTO polls(session_id,started,mono_start,state) VALUES (?,?,?,'in_progress')", (observer.session, self.clock.time(), self.clock.monotonic()))
        self.clock.advance(60)
        observer = self.observer()
        self.rpc.pool = []
        self.rpc.tip = 103
        self.rpc.blocks[101] = [identity('A')]
        observer.cycle()
        result = observer.cycle()
        self.assertEqual(result['coverage']['sessions_total'], 2)
        self.assertEqual(result['coverage']['failed_polls'], 1)
        self.assertEqual(result['coverage']['polls_total'], 5)
        self.assertEqual(result['outcomes']['confirmed'], 1)
        self.assertEqual(result['outcomes']['confirmation_delay']['sample_count'], 0)
        interrupted = [poll for poll in result['poll_history'] if poll['error_type'] == 'ObserverRestart']
        self.assertEqual(len(interrupted), 1)
        self.assertIsNone(interrupted[0]['duration_seconds'])
        self.assertEqual(len(self.rows('SELECT DISTINCT first_session FROM transactions')), 1)

    def test_private_source_gate_and_changed_source_refusal(self):
        self.args.source_mode = 'private_node'
        observer = self.observer()
        failed = observer.cycle()
        self.assertEqual(failed['state'], 'paused')
        self.assertEqual(failed['outcomes']['tracked_transactions'], 0)
        self.rpc.info_changes = {'version': '0.18.5.1', 'restricted': False}
        good = observer.cycle()
        self.assertEqual(good['source']['node_version'], '0.18.5.1')
        self.rpc.info_changes['version'] = '0.18.5.2'
        changed = observer.cycle()
        self.assertEqual(changed['state'], 'paused')
        self.assertEqual(changed['source']['node_version'], '0.18.5.1')
        before = Path(self.args.db).read_bytes()
        self.args.node = 'https://different.invalid'
        with self.assertRaisesRegex(ValueError, 'separate cohort'):
            self.observer()
        self.assertEqual(Path(self.args.db).read_bytes(), before)

    def test_private_gate_rejects_bootstrap_unsynced_and_unknown_version(self):
        self.args.source_mode = 'private_node'
        observer = self.observer()
        for change in [{'untrusted': True}, {'synchronized': False}, {'busy_syncing': True},
                       {'height_without_bootstrap': 50}, {'bootstrap_daemon_address': 'hidden.invalid'},
                       {'version': ''}, {'mainnet': False}, {'restricted': True}]:
            self.rpc.info_changes = {'version': '0.18.5.1', 'restricted': False, **change}
            self.assertEqual(observer.cycle()['state'], 'paused')
        self.assertEqual(self.rows('SELECT COUNT(*) AS n FROM blocks')[0]['n'], 0)

    def test_node_restart_and_wall_clock_adjustment_are_visible_gaps(self):
        self.rpc.info_changes['start_time'] = 1799999000
        observer = self.observer()
        observer.cycle()
        self.rpc.pool = [transaction('A')]
        self.clock.advance(60)
        observer.cycle()
        self.rpc.info_changes['start_time'] = 1799999050
        self.rpc.pool.append(transaction('B'))
        self.clock.advance(60)
        result = observer.cycle()
        self.assertTrue(result['source']['node_start_time_known'])
        self.assertEqual(result['source']['daemon_instance_identity'], 'unknown')
        self.assertEqual(result['outcomes']['cold_start_transactions'], 1)
        self.assertEqual(result['outcomes']['followup_censored_transactions'], 1)
        self.assertIn('node_start_time_changed', [gap['kind'] for gap in result['gaps']])
        self.clock.utc -= 30
        result = observer.cycle()
        self.assertIn('wall_clock_adjustment', [gap['kind'] for gap in result['gaps']])
        self.assertEqual(result['outcomes']['followup_censored_transactions'], 2)

    def test_tracking_observation_and_time_retention_limits_are_explicit(self):
        self.args.max_tracked_transactions = 10
        self.args.max_observations = 10
        self.args.retain_polls = self.args.retain_blocks = 2
        self.args.followup_seconds, self.args.retention_seconds = 60, 120
        self.rpc.pool = [transaction(i) for i in range(11)]
        observer = self.observer()
        result = observer.cycle()
        self.assertEqual(result['outcomes']['tracked_transactions'], 10)
        self.assertEqual(result['current']['transaction_count'], 11)
        self.assertEqual(result['current']['untracked_due_limit'], 1)
        self.assertFalse(result['current']['tracking_complete'])
        self.clock.advance(61)
        result = observer.cycle()
        self.assertEqual(result['outcomes']['censored'], 10)
        self.assertEqual(result['coverage']['retained_observations'], 10)
        self.clock.advance(61)
        result = observer.cycle()
        self.assertEqual(result['coverage']['pruned_transactions'], 10)
        self.assertEqual(result['outcomes']['cold_start_transactions'], 10)
        self.assertLessEqual(len(self.rows('SELECT * FROM polls')), 2)
        self.assertLessEqual(len(self.rows('SELECT * FROM blocks')), 2)
        self.assertGreaterEqual(min(row['first_seen'] for row in self.rows('SELECT first_seen FROM transactions')), self.clock.time() - 120)
        states = result['outcomes']
        self.assertEqual(states['tracked_transactions'], sum(states[key] for key in ('pending', 'disappeared', 'confirmed', 'censored')))

    def test_first_observation_timestamp_is_pool_fetch_completion(self):
        observer = self.observer()
        original_info = self.rpc.get_info
        original_pool = self.rpc.get_pool
        def info():
            self.clock.advance(7)
            return original_info()
        def pool():
            self.clock.advance(2)
            return original_pool()
        self.rpc.get_info, self.rpc.get_pool = info, pool
        self.rpc.pool = [transaction('A')]
        start = self.clock.time()
        result = observer.cycle()
        self.assertEqual(self.rows('SELECT first_seen FROM transactions')[0]['first_seen'], start + 9)
        self.assertEqual(result['poll_history'][0]['pool_fetch_seconds'], 2)
        self.assertEqual(result['poll_history'][0]['duration_seconds'], 16)

    def test_node_info_brackets_reject_identity_and_chain_inconsistency(self):
        observer = self.observer()
        observer.cycle()
        self.rpc.pool = [transaction('A')]
        first = self.rpc.get_info()
        for change in [{'version': 'unexpected-version'}, {'height': 100},
                       {'top_block_hash': identity('different-tip')}]:
            self.rpc.get_info = Mock(side_effect=[first, {**first, **change}])
            result = observer.cycle()
            self.assertEqual(result['state'], 'paused')
            self.assertEqual(result['current']['transaction_count'], 0)
            self.assertEqual(result['outcomes']['tracked_transactions'], 0)
        self.rpc.get_info = Mock(side_effect=[first, {**first, 'height': 102, 'top_block_hash': identity('block-101')}])
        self.rpc.changed_height = 100
        self.assertEqual(observer.cycle()['state'], 'paused')
        self.rpc.changed_height = None
        self.rpc.get_info = Mock(side_effect=[first, {**first, 'height': 102, 'top_block_hash': identity('block-101')}])
        result = observer.cycle()
        self.assertEqual(result['outcomes']['tracked_transactions'], 1)
        self.assertEqual(self.rows('SELECT first_tip FROM transactions')[0]['first_tip'], 101)

    def test_restart_needs_two_successful_polls_to_leave_warmup(self):
        observer = self.observer()
        observer.cycle()
        self.assertEqual(observer.cycle()['state'], 'observing')
        observer = self.observer()
        self.assertEqual(observer.cycle()['state'], 'warming_up')
        self.assertEqual(observer.cycle()['state'], 'observing')

    def test_unknown_database_public_storage_and_atomic_publication_failure(self):
        database = Path(self.args.db)
        database.parent.mkdir()
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute('CREATE TABLE historical(value)')
        before = database.read_bytes()
        with self.assertRaisesRegex(ValueError, 'not identified'):
            self.observer()
        self.assertEqual(database.read_bytes(), before)
        self.args.db = str(self.root / 'public/observer.db')
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.observer()
        self.args.db = str(self.root / 'private/new.db')
        observer = self.observer()
        observer.cycle()
        published = observer.path.read_bytes()
        with patch('pathlib.Path.replace', side_effect=OSError('Synthetic rename failure')):
            with self.assertRaises(OSError): observer.cycle()
        self.assertEqual(observer.path.read_bytes(), published)
        self.assertEqual([path.name for path in observer.output_dir.iterdir()], ['pool-observations.json'])

    def test_rpc_request_budget_byte_limit_and_redacted_transport_errors(self):
        rpc = PoolRPC(self.args)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b'{"status":"OK","transactions":[]}']
        rpc.session.post = Mock(return_value=response)
        self.assertEqual(rpc.get_pool()['transactions'], [])
        self.assertTrue(rpc.session.post.call_args.args[0].endswith('/get_transaction_pool'))
        self.assertLessEqual(rpc.session.post.call_args.kwargs['timeout'], self.args.rpc_timeout_seconds)
        rpc.requests_used = self.args.max_requests
        with self.assertRaisesRegex(RPCError, 'budget'): rpc.get_pool()
        rpc.begin_cycle()
        response.iter_content.return_value = [b'X' * (self.args.max_response_bytes + 1)]
        with self.assertRaisesRegex(RPCError, 'byte limit'): rpc.get_pool()
        rpc.begin_cycle()
        response.iter_content.return_value = [b'not JSON']
        with self.assertRaisesRegex(RPCError, 'transport/JSON') as error: rpc.get_pool()
        self.assertNotIn('secret', str(error.exception))

    def test_cli_lock_refuses_parallel_writer_before_opening_database(self):
        with patch('pool_observer.parse_args', return_value=self.args), \
             patch('pool_observer.logging.basicConfig'), \
             patch('pool_observer.signal.signal'), \
             patch('pool_observer.fcntl.flock', side_effect=BlockingIOError), \
             patch('pool_observer.PoolObserver') as constructor:
            with self.assertRaisesRegex(SystemExit, 'Another pool observer'):
                main()
            constructor.assert_not_called()
        self.assertFalse(Path(self.args.db).exists())

    def test_pruning_clears_old_private_transaction_content(self):
        self.args.followup_seconds, self.args.retention_seconds = 60, 120
        self.rpc.pool = [transaction('private-unique-marker')]
        observer = self.observer()
        observer.cycle()
        self.assertIn(identity('private-unique-marker').encode(), Path(self.args.db).read_bytes())
        self.rpc.pool = []
        self.clock.advance(121)
        result = observer.cycle()
        self.assertEqual(result['outcomes']['tracked_transactions'], 0)
        self.assertNotIn(identity('private-unique-marker').encode(), Path(self.args.db).read_bytes())


if __name__ == '__main__':
    unittest.main()
