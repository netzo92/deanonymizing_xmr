"""Read-only reconciliation on the deployed GCP VM; prints aggregate evidence."""
import json,sqlite3,subprocess,hashlib
from pathlib import Path
from datetime import datetime,timezone
root=Path('/var/www/xmr')
p=json.loads((root/'pool-observations.json').read_text())
c=sqlite3.connect('file:/var/lib/xmr-pool/pool_observations.db?mode=ro',uri=True,timeout=3)
c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
meta={k:json.loads(v) for k,v in c.execute('SELECT key,value FROM meta')}
counts=dict(c.execute('SELECT state,COUNT(*) FROM transactions GROUP BY state'))
checks={'database_integrity':c.execute('PRAGMA quick_check(1)').fetchone()[0]=='ok','current_snapshot':p['current']==meta['current']}
for field in ('pending','disappeared','confirmed','censored'):checks[field]=p['outcomes'][field]==counts.get(field,0)
for field in ('polls_total','successful_polls','failed_polls','sessions_total'):checks[field]=p['coverage'][field]==meta.get(field,0)
checks['tracked_partition']=sum(counts.values())==p['outcomes']['tracked_transactions']
checks['eligible_delays']=p['outcomes']['confirmation_delay']['sample_count']==c.execute('SELECT COUNT(*) FROM transactions WHERE state="confirmed" AND delay_seconds IS NOT NULL').fetchone()[0]
checks['block_matches']=p['outcomes']['observed_confirmations']==c.execute('SELECT COALESCE(SUM(tracked_matches),0) FROM blocks').fetchone()[0]
checks['block_transactions']=p['outcomes']['confirmed_block_transactions']==c.execute('SELECT COALESCE(SUM(tx_count),0) FROM blocks').fetchone()[0]
checks['receipt_partition']=p['current']['receive_time_known']+p['current']['receive_time_unknown']==p['current']['transaction_count']
checks['last_success']=p['last_success_at']==datetime.fromtimestamp(meta['last_success_at'],timezone.utc).isoformat()
c.rollback();c.close()
services={}
for service in ('xmr-collector','xmr-live-observer','xmr-pool-observer'):
 raw=subprocess.check_output(['systemctl','show',service+'.service','--property=MainPID,ActiveState,MemoryCurrent'],text=True)
 services[service]=dict(line.split('=',1) for line in raw.strip().splitlines())
release=json.loads((root/'pool-release.json').read_text())
checks['source_revision']=p['source']['source_revision']==release['source_commit']
checks['historical_writer_preserved']=int(services['xmr-collector']['MainPID'])==release['collector_pid_before']
checks['block_writer_preserved']=int(services['xmr-live-observer']['MainPID'])==release['live_observer_pid_before']
checks['runtime_hash']=p['source']['observer_sha256']==hashlib.sha256(Path('/opt/xmr/pool-current/pool_observer.py').read_bytes()).hexdigest()
report={'checked_at':datetime.now(timezone.utc).isoformat(),'checks':checks,'services':services,'snapshot':p,'database_bytes':Path('/var/lib/xmr-pool/pool_observations.db').stat().st_size}
print(json.dumps(report,indent=2))
if not all(checks.values()):raise SystemExit('Pool snapshot did not reconcile; inspect concurrent update or failing check')
