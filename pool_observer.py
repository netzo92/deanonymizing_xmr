"""Bounded prospective pool sightings and block-confirmation joins.

Only aggregate observations are public. The separate private SQLite store keeps
public transaction identities and local timing records, never blobs or peers.
"""

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import threading
import time
import uuid

from live_observer import ObserverRPC, atomic_json, source_origin, valid_header
from monero_rpc import RPCError

LOG = logging.getLogger(__name__)
APPLICATION_ID = 0x5447504F
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,started REAL NOT NULL,monotonic_start REAL NOT NULL,
 observer_sha256 TEXT,source_revision TEXT);
CREATE TABLE IF NOT EXISTS polls(id INTEGER PRIMARY KEY,session_id TEXT,started REAL,ended REAL,
 mono_start REAL,mono_end REAL,state TEXT,error_type TEXT,pool_count INTEGER,pool_complete INTEGER,
 node_start_time INTEGER,pool_read_started REAL,pool_read_ended REAL,pool_mono_start REAL,pool_mono_end REAL);
CREATE TABLE IF NOT EXISTS transactions(tx_hash TEXT PRIMARY KEY,first_seen REAL,last_seen REAL,
 first_session TEXT,last_session TEXT,first_mono REAL,last_mono REAL,first_tip INTEGER,
 fee TEXT,weight TEXT,state TEXT,cold_start INTEGER,followup_censored INTEGER DEFAULT 0,
 censor_reason TEXT,confirmed_height INTEGER,confirmed_hash TEXT,confirmed_seen REAL,
 confirmed_session TEXT,confirmed_mono REAL,delay_seconds REAL,reappearances INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,poll_id INTEGER,tx_hash TEXT,
 seen REAL,monotonic_seen REAL,node_receive_time INTEGER,fee TEXT,weight TEXT,
 UNIQUE(poll_id,tx_hash));
CREATE INDEX IF NOT EXISTS observations_tx ON observations(tx_hash,id);
CREATE TABLE IF NOT EXISTS blocks(height INTEGER PRIMARY KEY,hash TEXT,previous_hash TEXT,
 chain_timestamp INTEGER,observed REAL,tx_count INTEGER,tracked_matches INTEGER,
 seen_before_block INTEGER);
CREATE TABLE IF NOT EXISTS gaps(id INTEGER PRIMARY KEY,kind TEXT,recorded REAL,
 from_height INTEGER,to_height INTEGER);
"""


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def integer(value, minimum=0, maximum=2**64 - 1):
    return type(value) is int and minimum <= value <= maximum


def tx_identity(value):
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None


def observer_revision():
    revision = Path(__file__).with_name('REVISION')
    value = revision.read_text().strip() if revision.is_file() else None
    return value if isinstance(value, str) and re.fullmatch('[a-f0-9]{40}', value) else None


class Paused(RPCError):
    pass


class PoolRPC(ObserverRPC):
    def get_info(self):
        return self._request('/json_rpc', {'jsonrpc': '2.0', 'id': 'pool', 'method': 'get_info'}).get('result')

    def get_pool(self):
        return self._request('/get_transaction_pool', {})


def parse_pool(response, limit):
    """Reject incomplete/over-limit snapshots; whitelist only needed scalar data."""
    if not isinstance(response, dict) or response.get('status') != 'OK':
        raise RPCError('Pool snapshot has an unsuccessful status')
    # Epee serialize_stl_container_t_obj omits empty vectors. An absent field
    # in an otherwise successful response is the upstream empty-pool encoding.
    entries = response.get('transactions', [])
    if not isinstance(entries, list):
        raise RPCError('Pool transaction list is invalid')
    if len(entries) > limit:
        raise RPCError('Pool transaction limit exceeded; snapshot not ingested')
    result, seen = [], set()
    for tx in entries:
        if (not isinstance(tx, dict) or not tx_identity(tx.get('id_hash'))
                or tx['id_hash'].lower() in seen or not integer(tx.get('fee'))
                or not integer(tx.get('weight'), 1)):
            raise RPCError('Pool transaction identity, fee or weight is invalid')
        identity = tx['id_hash'].lower()
        seen.add(identity)
        receipt = tx.get('receive_time')
        # Zero is the restricted-RPC redaction value, never an epoch arrival.
        receipt = receipt if integer(receipt, 1, 253402300799) else None
        result.append({'tx_hash': identity, 'fee': tx['fee'], 'weight': tx['weight'], 'receive_time': receipt})
    return result


class PoolObserver:
    def __init__(self, args, rpc=None, clock=None):
        self.args, self.clock = args, clock or time
        self.db_path, self.output_dir = Path(args.db).resolve(), Path(args.output_dir).resolve()
        if self.db_path.is_relative_to(self.output_dir):
            raise ValueError('Pool database must be outside the public web directory')
        self.origin = source_origin(args.node)
        binding = hashlib.sha256((args.source_mode + '\n' + args.node.rstrip('/')).encode()).hexdigest()
        if self.db_path.exists():
            with closing(sqlite3.connect(self.db_path.as_uri() + '?mode=ro&immutable=1', uri=True)) as reader:
                if reader.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
                    raise ValueError('Refusing a database not identified as a pool observer database')
                if self.get(reader, 'source_binding') != binding:
                    raise ValueError('Source endpoint or mode changed; use a separate cohort database')
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self.db_path.touch(mode=0o600, exist_ok=False)
        self.path = self.output_dir / 'pool-observations.json'
        self.rpc = rpc or PoolRPC(args)
        self.session = uuid.uuid4().hex
        now, mono = self.clock.time(), self.clock.monotonic()
        with closing(self.connect()) as conn, conn:
            conn.execute(f'PRAGMA application_id={APPLICATION_ID}')
            conn.executescript(SCHEMA)
            existed = self.get(conn, 'cohort_id') is not None
            if not existed:
                self.set(conn, 'cohort_id', uuid.uuid4().hex)
                self.set(conn, 'source_binding', binding)
                self.set(conn, 'first_observed_at', now)
            conn.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', (self.session, now, mono,
                         hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), observer_revision()))
            self.set(conn, 'sessions_total', self.get(conn, 'sessions_total', 0) + 1)
            if existed:
                self.gap(conn, 'observer_restart', now)
                self.censor_active(conn, 'observer_restart')
                interrupted = conn.execute("UPDATE polls SET state='error',error_type='ObserverRestart',ended=? WHERE state='in_progress'", (now,)).rowcount
                if interrupted:
                    self.set(conn, 'polls_total', self.get(conn, 'polls_total', 0) + interrupted)
                    self.set(conn, 'failed_polls', self.get(conn, 'failed_polls', 0) + interrupted)
            self.set(conn, 'session_has_snapshot', False)
            self.set(conn, 'session_successful_polls', 0)
        self.db_path.chmod(0o600)

    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('PRAGMA secure_delete=ON')
        # Summary/timing-only DB, bounded to 256 MiB with ordinary 4-KiB pages.
        conn.execute('PRAGMA max_page_count=65536')
        return conn

    @staticmethod
    def get(conn, key, default=None):
        row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set(conn, key, value):
        conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value, allow_nan=False)))

    @staticmethod
    def gap(conn, kind, now, start=None, end=None):
        conn.execute('INSERT INTO gaps(kind,recorded,from_height,to_height) VALUES (?,?,?,?)', (kind, now, start, end))

    @staticmethod
    def censor_active(conn, reason):
        conn.execute("UPDATE transactions SET followup_censored=1,censor_reason=COALESCE(censor_reason,?) WHERE state IN ('pending','disappeared')", (reason,))

    def check_info(self, conn, info):
        if (not isinstance(info, dict) or info.get('status') != 'OK'
                or not integer(info.get('height'), 1, 2**53 - 1)
                or not tx_identity(info.get('top_block_hash'))):
            raise RPCError('Node identity or chain-tip metadata is invalid')
        if info.get('mainnet') is not True or info.get('nettype', 'mainnet') != 'mainnet' or info.get('testnet') or info.get('stagenet'):
            raise Paused('Pool observer requires a mainnet source')
        restricted = info.get('restricted') if type(info.get('restricted')) is bool else None
        version = info.get('version')
        version = version if isinstance(version, str) and version.strip() and len(version) <= 128 else None
        active_bootstrap = (True if info.get('untrusted') or info.get('bootstrap_daemon_address') else
                            False if info.get('untrusted') is False else None)
        start_time = info.get('start_time')
        start_time = start_time if integer(start_time, 1, 253402300799) else None
        source = {'mode': self.args.source_mode, 'origin': self.origin, 'network': 'mainnet',
                  'node_version': version, 'restricted': restricted,
                  'synchronized': info.get('synchronized') if type(info.get('synchronized')) is bool else None,
                  'bootstrap': active_bootstrap, 'untrusted': info.get('untrusted') if type(info.get('untrusted')) is bool else None,
                  'node_start_time_known': start_time is not None, 'daemon_instance_identity': 'unknown'}
        if self.args.source_mode == 'private_node':
            if (restricted is not False or not source['synchronized'] or info.get('busy_syncing') is not False
                    or info.get('untrusted') is not False or active_bootstrap or version is None
                    or info.get('height_without_bootstrap') != info['height']):
                raise Paused('Private source must be synchronized, unrestricted, versioned mainnet without bootstrap')
        descriptor = {key: source[key] for key in ('mode', 'network', 'node_version', 'restricted')}
        previous = self.get(conn, 'node_descriptor')
        if previous is not None and previous != descriptor:
            raise Paused('Node version or pool visibility changed; create a separate cohort database')
        old_start = self.get(conn, 'node_start_time')
        if old_start is not None and start_time != old_start:
            self.gap(conn, 'node_start_time_changed', self.clock.time())
            self.censor_active(conn, 'node_start_time_changed')
            self.set(conn, 'session_has_snapshot', False)
        self.set(conn, 'node_start_time', start_time)
        self.set(conn, 'node_descriptor', descriptor)
        self.set(conn, 'source', source)
        return info['height'] - 1

    def ingest_pool(self, conn, entries, poll_id, tip, now, mono, reported):
        cold = not self.get(conn, 'session_has_snapshot', False) or self.get(conn, 'last_poll_failed', False)
        last = self.get(conn, 'last_pool_at')
        if last is not None and now - last > self.args.interval_seconds * 2 + self.args.cycle_budget_seconds:
            self.gap(conn, 'poll_interval_gap', now)
            self.censor_active(conn, 'poll_interval_gap')
            cold = True
        if last is not None and self.get(conn, 'last_pool_session') == self.session:
            clock_difference = (now - last) - (mono - self.get(conn, 'last_pool_mono'))
            if abs(clock_difference) > 5:
                self.gap(conn, 'wall_clock_adjustment', now)
                self.censor_active(conn, 'wall_clock_adjustment')
                cold = True
        prior_states = dict(conn.execute('SELECT tx_hash,state FROM transactions'))
        prior_seen = set(self.get(conn, 'last_pool_hashes', []))
        conn.execute("UPDATE transactions SET state='disappeared' WHERE state='pending'")
        available = self.args.max_tracked_transactions - conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
        dropped = 0
        for tx in entries:
            identity = tx['tx_hash']
            row = conn.execute('SELECT state FROM transactions WHERE tx_hash=?', (identity,)).fetchone()
            if row is None:
                if available <= 0:
                    dropped += 1
                    continue
                conn.execute('INSERT INTO transactions(tx_hash,first_seen,last_seen,first_session,last_session,first_mono,last_mono,first_tip,fee,weight,state,cold_start) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                             (identity, now, now, self.session, self.session, mono, mono, tip, str(tx['fee']), str(tx['weight']), 'pending', int(cold or identity in prior_seen)))
                available -= 1
            else:
                if row['state'] == 'confirmed':
                    raise Paused('A followed confirmed transaction reappeared in the pool; source/reorganization review required')
                conn.execute("UPDATE transactions SET last_seen=?,last_session=?,last_mono=?,fee=?,weight=?,reappearances=reappearances+?,state=CASE WHEN state='censored' THEN state ELSE 'pending' END WHERE tx_hash=?",
                             (now, self.session, mono, str(tx['fee']), str(tx['weight']), int(prior_states[identity] == 'disappeared'), identity))
            conn.execute('INSERT INTO observations(poll_id,tx_hash,seen,monotonic_seen,node_receive_time,fee,weight) VALUES (?,?,?,?,?,?,?)',
                         (poll_id, identity, now, mono, tx['receive_time'], str(tx['fee']), str(tx['weight'])))
        self.set(conn, 'current', {'snapshot_at': stamp(now), 'transaction_count': len(entries),
                 'reported_pool_count': reported if integer(reported, 0, 2**53 - 1) else None,
                 'complete': True, 'tracking_complete': dropped == 0, 'untracked_due_limit': dropped,
                 'fee_atomic_total': str(sum(tx['fee'] for tx in entries)), 'weight_total': str(sum(tx['weight'] for tx in entries)),
                 'receive_time_known': sum(tx['receive_time'] is not None for tx in entries),
                 'receive_time_unknown': sum(tx['receive_time'] is None for tx in entries)})
        if dropped:
            self.gap(conn, 'tracking_capacity', now)
        self.set(conn, 'last_pool_at', now)
        self.set(conn, 'last_pool_mono', mono)
        self.set(conn, 'last_pool_session', self.session)
        self.set(conn, 'last_pool_hashes', [tx['tx_hash'] for tx in entries])
        self.set(conn, 'session_has_snapshot', True)
        self.set(conn, 'last_poll_failed', False)
        return dropped

    def scan_confirmations(self, conn, tip):
        target = tip - self.args.confirmations
        self.set(conn, 'confirmed_target_height', target)
        tail = conn.execute('SELECT height,hash FROM blocks ORDER BY height DESC LIMIT 1').fetchone()
        if tail:
            if tail['height'] > tip:
                raise Paused('Node is behind the stored confirmation tail')
            check = valid_header(self.rpc.get_block_header(tail['height']), tail['height'])
            if check['hash'] != tail['hash']:
                raise Paused('Confirmed tail changed; source/reorganization review required')
            start = tail['height'] + 1
        else:
            start = self.get(conn, 'initial_block_from_height')
            if start is None:
                start = max(0, target - self.args.initial_blocks + 1)
                self.set(conn, 'initial_block_from_height', start)
        start = max(start, self.get(conn, 'skipped_through_height', -1) + 1)
        window_start = max(0, target - self.args.catchup_window_blocks + 1)
        if start < window_start:
            self.gap(conn, 'confirmation_height_gap', self.clock.time(), start, window_start - 1)
            self.censor_active(conn, 'confirmation_height_gap')
            self.set(conn, 'skipped_through_height', window_start - 1)
            start = window_start
        conn.commit()
        for height in range(start, min(target, start + self.args.blocks_per_cycle - 1) + 1):
            block = self.rpc.get_block(height)
            header = valid_header(block.get('block_header') if isinstance(block, dict) else None, height)
            hashes = block.get('tx_hashes')
            if (not isinstance(hashes, list) or len(hashes) > self.args.max_block_transactions
                    or any(not tx_identity(value) for value in hashes)
                    or len({value.lower() for value in hashes}) != len(hashes)
                    or 'num_txes' in header and (not integer(header['num_txes']) or header['num_txes'] != len(hashes))):
                raise RPCError('Invalid or over-limit block transaction membership')
            previous = conn.execute('SELECT hash FROM blocks WHERE height=?', (height - 1,)).fetchone()
            if previous and previous[0] != header['prev_hash']:
                raise Paused('Block linkage changed; source/reorganization review required')
            now, mono = self.clock.time(), self.clock.monotonic()
            matches, before = 0, 0
            with conn:
                for identity in hashes:
                    row = conn.execute('SELECT * FROM transactions WHERE tx_hash=?', (identity.lower(),)).fetchone()
                    if row is None:
                        continue
                    if row['confirmed_height'] is not None:
                        raise Paused('A transaction appears in multiple followed blocks; source review required')
                    matches += 1
                    prospective = height > row['first_tip']
                    before += int(prospective)
                    delay = None
                    if prospective and not row['cold_start'] and not row['followup_censored'] and row['first_session'] == self.session and mono >= row['first_mono']:
                        delay = mono - row['first_mono']
                    conn.execute("UPDATE transactions SET state='confirmed',confirmed_height=?,confirmed_hash=?,confirmed_seen=?,confirmed_session=?,confirmed_mono=?,delay_seconds=? WHERE tx_hash=?",
                                 (height, header['hash'], now, self.session, mono, delay, identity.lower()))
                conn.execute('INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?)',
                             (height, header['hash'], header['prev_hash'], header['timestamp'], now, len(hashes), matches, before))

    def prune(self, conn, now):
        conn.execute("UPDATE transactions SET state='censored',followup_censored=1,censor_reason=COALESCE(censor_reason,'followup_horizon') WHERE state IN ('pending','disappeared') AND first_seen<?", (now - self.args.followup_seconds,))
        expired = conn.execute("SELECT COUNT(*) FROM transactions WHERE first_seen<? AND state IN ('confirmed','censored')", (now - self.args.retention_seconds,)).fetchone()[0]
        if expired:
            self.set(conn, 'pruned_transactions', self.get(conn, 'pruned_transactions', 0) + expired)
            conn.execute("DELETE FROM transactions WHERE first_seen<? AND state IN ('confirmed','censored')", (now - self.args.retention_seconds,))
        conn.execute('DELETE FROM observations WHERE seen<? OR tx_hash NOT IN (SELECT tx_hash FROM transactions)', (now - self.args.retention_seconds,))
        conn.execute('DELETE FROM polls WHERE started<? AND state!=\'in_progress\'', (now - self.args.retention_seconds,))
        for table, limit in (('observations', self.args.max_observations), ('polls', self.args.retain_polls), ('blocks', self.args.retain_blocks), ('gaps', 200), ('sessions', 100)):
            key = 'height' if table == 'blocks' else 'rowid' if table == 'sessions' else 'id'
            conn.execute(f'DELETE FROM {table} WHERE {key} NOT IN (SELECT {key} FROM {table} ORDER BY {key} DESC LIMIT ?)', (limit,))

    def cycle(self):
        started, mono_start = self.clock.time(), self.clock.monotonic()
        with closing(self.connect()) as conn:
            poll_id = conn.execute('INSERT INTO polls(session_id,started,mono_start,state,pool_complete) VALUES (?,?,?,?,0)', (self.session, started, mono_start, 'in_progress')).lastrowid
            conn.commit()
            failure = None
            try:
                if shutil.disk_usage(self.db_path.parent).free < self.args.min_free_mb * 1024**2:
                    raise Paused('Insufficient free disk space')
                if hasattr(self.rpc, 'begin_cycle'):
                    self.rpc.begin_cycle()
                first = self.rpc.get_info()
                first_tip = self.check_info(conn, first)
                pool_started, pool_mono_start = self.clock.time(), self.clock.monotonic()
                response = self.rpc.get_pool()
                now, mono = self.clock.time(), self.clock.monotonic()
                entries = parse_pool(response, self.args.max_pool_transactions)
                info = self.rpc.get_info()
                tip = self.check_info(conn, info)
                if tip < first_tip or tip == first_tip and info['top_block_hash'].lower() != first['top_block_hash'].lower():
                    raise Paused('Chain tip changed inconsistently across the pool snapshot; source review required')
                if tip > first_tip:
                    anchor = valid_header(self.rpc.get_block_header(first_tip), first_tip)
                    if anchor['hash'].lower() != first['top_block_hash'].lower():
                        raise Paused('Chain ancestry changed across the pool snapshot; source review required')
                if self.args.source_mode == 'private_node' and response.get('untrusted') is not False:
                    raise Paused('Private pool response is bootstrap-derived or lacks provenance')
                source = self.get(conn, 'source')
                source['pool_untrusted'] = response.get('untrusted') if type(response.get('untrusted')) is bool else None
                self.set(conn, 'source', source)
                # Persist a complete pool snapshot separately from the block cursor.
                # A later block failure must not discard an already observed sighting.
                self.prune(conn, now)
                self.ingest_pool(conn, entries, poll_id, tip, now, mono, info.get('tx_pool_size'))
                self.set(conn, 'chain_tip', {'height': tip, 'hash': info['top_block_hash'], 'observed_at': stamp(self.clock.time())})
                conn.execute('UPDATE polls SET pool_count=?,pool_complete=1,node_start_time=?,pool_read_started=?,pool_read_ended=?,pool_mono_start=?,pool_mono_end=? WHERE id=?',
                             (len(entries), self.get(conn, 'node_start_time'), pool_started, now, pool_mono_start, mono, poll_id))
                conn.commit()
                self.scan_confirmations(conn, tip)
                self.set(conn, 'last_success_at', self.clock.time())
                self.set(conn, 'session_successful_polls', self.get(conn, 'session_successful_polls', 0) + 1)
            except Exception as error:
                conn.rollback()
                failure = {'type': type(error).__name__, 'message': str(error) if isinstance(error, RPCError) else 'Pool observer failed; inspect service logs.'}
                LOG.warning('Pool cycle failed (%s): %s', type(error).__name__, error)
                self.gap(conn, 'poll_failure', self.clock.time())
                self.censor_active(conn, 'poll_failure')
                self.set(conn, 'last_poll_failed', True)
            ended, mono_end = self.clock.time(), self.clock.monotonic()
            state = 'paused' if failure and failure['type'] == 'Paused' else 'error' if failure else 'observing'
            self.set(conn, 'last_error', failure)
            self.set(conn, 'last_state', state)
            self.set(conn, 'polls_total', self.get(conn, 'polls_total', 0) + 1)
            counter = 'failed_polls' if failure else 'successful_polls'
            self.set(conn, counter, self.get(conn, counter, 0) + 1)
            conn.execute('UPDATE polls SET ended=?,mono_end=?,state=?,error_type=? WHERE id=?', (ended, mono_end, state, failure['type'] if failure else None, poll_id))
            self.prune(conn, ended)
            conn.commit()
            payload = self.export(conn)
            atomic_json(self.path, payload)
            return payload

    def export(self, conn):
        txs = [dict(row) for row in conn.execute('SELECT state,cold_start,followup_censored,delay_seconds FROM transactions')]
        states = Counter(row['state'] for row in txs)
        delays = sorted(row['delay_seconds'] for row in txs if row['state'] == 'confirmed' and row['delay_seconds'] is not None)
        def quantile(fraction):
            if not delays: return None
            index = (len(delays) - 1) * fraction
            low = int(index); high = min(low + 1, len(delays) - 1)
            return delays[low] + (delays[high] - delays[low]) * (index - low)
        bins = [{'label': label, 'count': sum(low <= value < high for value in delays)} for label, low, high in
                (('< 1 minute', 0, 60), ('1–5 minutes', 60, 300), ('5–15 minutes', 300, 900), ('15–60 minutes', 900, 3600), ('1 hour or more', 3600, float('inf')))]
        source = self.get(conn, 'source', {'mode': self.args.source_mode, 'origin': self.origin, 'network': None, 'node_version': None, 'restricted': None, 'synchronized': None, 'bootstrap': None})
        source.update(cohort_id=self.get(conn, 'cohort_id'), source_revision=observer_revision(),
                      observer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        tail = conn.execute('SELECT MAX(height) FROM blocks').fetchone()[0]
        target = self.get(conn, 'confirmed_target_height')
        session = conn.execute('SELECT started FROM sessions WHERE id=?', (self.session,)).fetchone()
        history = [dict(row) for row in conn.execute('SELECT started,mono_end-mono_start AS duration_seconds,pool_mono_end-pool_mono_start AS pool_fetch_seconds,state,pool_count,pool_complete,error_type FROM polls ORDER BY id DESC LIMIT 120')]
        for row in history:
            row['started_at'] = stamp(row.pop('started'))
            row['pool_complete'] = bool(row['pool_complete'])
        history.reverse()
        joined = conn.execute('SELECT COALESCE(SUM(tracked_matches),0),COALESCE(SUM(seen_before_block),0),COALESCE(SUM(tx_count),0) FROM blocks').fetchone()
        gaps = [dict(row) for row in conn.execute('SELECT kind,recorded,from_height,to_height FROM gaps ORDER BY id DESC LIMIT 100')]
        for gap in gaps:gap['recorded_at'] = stamp(gap.pop('recorded'))
        outcomes = {key: states[key] for key in ('pending', 'disappeared', 'confirmed', 'censored')}
        outcomes.update(tracked_transactions=len(txs), cold_start_transactions=sum(row['cold_start'] for row in txs),
                        followup_censored_transactions=sum(row['followup_censored'] for row in txs), observed_confirmations=joined[0],
                        confirmations_seen_before_block=joined[1], confirmed_block_transactions=joined[2],
                        confirmation_delay={'sample_count': len(delays), 'median_seconds': quantile(.5), 'p90_seconds': quantile(.9), 'bins': bins,
                        'excluded_count': states['confirmed'] - len(delays),
                        'basis': 'First local pool observation to local confirmed-block detection; monotonic same-session, non-cold-start, uninterrupted follow-up only. Includes polling and confirmation lag; not mining or network propagation latency.'})
        state = self.get(conn, 'last_state', 'warming_up')
        if state == 'observing' and (self.get(conn, 'session_successful_polls', 0) < 2 or tail is None or target is not None and tail < target):state = 'warming_up'
        return {'schema_version': 1, 'state': state, 'generated_at': stamp(self.clock.time()),
                'last_success_at': stamp(self.get(conn, 'last_success_at')), 'source': source,
                'current': self.get(conn, 'current'), 'outcomes': outcomes,
                'coverage': {'first_observed_at': stamp(self.get(conn, 'first_observed_at')), 'session_started_at': stamp(session[0]) if session else None,
                    'sessions_total': self.get(conn, 'sessions_total', 0), 'polls_total': self.get(conn, 'polls_total', 0),
                    'session_successful_polls': self.get(conn, 'session_successful_polls', 0),
                    'successful_polls': self.get(conn, 'successful_polls', 0), 'failed_polls': self.get(conn, 'failed_polls', 0),
                    'initial_block_from_height': self.get(conn, 'initial_block_from_height'), 'initial_window_left_truncated': True,
                    'confirmed_target_height': target, 'last_processed_height': tail, 'blocks_behind': max(0, target - tail) if target is not None and tail is not None else None,
                    'pruned_transactions': self.get(conn, 'pruned_transactions', 0), 'last_poll_complete': history[-1]['pool_complete'] if history else False,
                    'retained_observations': conn.execute('SELECT COUNT(*) FROM observations').fetchone()[0]},
                'chain_tip': self.get(conn, 'chain_tip'), 'poll_history': history, 'gaps': gaps, 'error': self.get(conn, 'last_error'),
                'limits': {key: getattr(self.args, key) for key in vars(self.args) if key not in {'db', 'output_dir', 'node', 'source_mode', 'once'}},
                'interpretation': [
                    'Prospective feasibility pilot; no forecast model or forecast improvement is established.',
                    'Raw pool sightings and transaction records use a rolling 24-hour default retention, with additional row and storage limits; this pilot is not a permanent study archive.',
                    'An RPC endpoint may be load balanced. Endpoint identity is not a known daemon instance; version and start time can be redacted. Detected node changes censor follow-up.',
                    'Pool disappearance is not confirmation. Confirmation requires transaction membership in a followed block.',
                    'Pool receipt times belong to the source node, may change with relay state, and zero/redacted values remain unknown. Raw records are private.',
                    'The two node-info reads and pool request are not an atomic node snapshot. Reported pool count may differ from the observed list.',
                    'Public restricted RPC observes a different pool visibility population from a private unrestricted node; cohorts must remain separate.',
                    'Current totals describe the last complete pool list; transaction states and delay samples cover retained tracked records. Confirmation coverage totals cover retained followed blocks.',
                    'Initial chain coverage is left-truncated; startup sightings, observation gaps, cross-session timings and incomplete follow-up are excluded from the delay sample.',
                    'This records activity and local detection timing, not sender identity, real ring members, or wallet ownership.']}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('db', 'output_dir', 'node'):parser.add_argument('--' + key.replace('_', '-'), required=True)
    parser.add_argument('--source-mode', choices=('public_rpc', 'private_node'), default='public_rpc')
    defaults = {'interval_seconds': (60, 15, 3600), 'confirmations': (2, 0, 100), 'blocks_per_cycle': (6, 1, 24),
        'initial_blocks': (6, 1, 120), 'catchup_window_blocks': (120, 1, 1440), 'max_pool_transactions': (1000, 1, 5000),
        'max_block_transactions': (2000, 1, 10000), 'max_tracked_transactions': (20000, 10, 100000),
        'max_observations': (200000, 10, 1000000), 'retain_polls': (1440, 2, 10000), 'retain_blocks': (1440, 2, 5000),
        'followup_seconds': (21600, 60, 604800), 'retention_seconds': (86400, 60, 604800), 'max_requests': (24, 4, 100),
        'cycle_budget_seconds': (45, 5, 120), 'rpc_timeout_seconds': (10, 1, 15), 'max_response_bytes': (8388608, 1024, 16777216), 'min_free_mb': (250, 1, 10000)}
    for key, (default, _, _) in defaults.items():parser.add_argument('--'+key.replace('_','-'), type=int, default=default)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    for key, (_, low, high) in defaults.items():
        if not low <= getattr(args,key) <= high:parser.error(f'{key} must be between {low} and {high}')
    if args.initial_blocks > args.catchup_window_blocks or args.followup_seconds > args.retention_seconds:
        parser.error('Initial window must fit catch-up; follow-up must fit retention')
    source_origin(args.node)
    return args


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):signal.signal(signum, lambda *_: stop.set())
    lock_path = Path(args.db).resolve().with_suffix('.pool.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Another pool observer owns this database')
        observer = PoolObserver(args)
        while not stop.is_set():
            payload = observer.cycle()
            LOG.info('Pool observer %s; %s retained transactions', payload['state'], payload['outcomes']['tracked_transactions'])
            if args.once:
                if payload['state'] in {'error','paused'}:raise SystemExit(1)
                break
            stop.wait(args.interval_seconds)


if __name__ == '__main__':main()
