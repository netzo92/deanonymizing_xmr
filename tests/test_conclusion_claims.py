"""Deliberate evidence/display drift must fail before publication."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from research.check_conclusion_claims import validate

ROOT = Path(__file__).resolve().parents[1]


class ConclusionClaimTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        manifest=json.loads((ROOT/'research/conclusion-claims.json').read_text())
        for path in ['research/conclusion-claims.json','docs/research-progress.json',*[a['path'] for a in manifest['artifacts'].values()]]:
            target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/path,target)

    def test_all_frozen_claims_reconcile(self):
        result=validate(self.root);self.assertEqual(result['artifacts'],3);self.assertGreater(result['claims'],70)

    def test_source_number_change_fails_even_after_hash_is_updated(self):
        path=self.root/'research/results/output_origins_2026-09-11.json';data=json.loads(path.read_text());data['summary']['age_blocks_max']+=1;path.write_text(json.dumps(data))
        self.assertRaisesRegex(ValueError,'hash drift',validate,self.root)
        manifest=self.root/'research/conclusion-claims.json';data=json.loads(manifest.read_text());data['artifacts']['origin']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();manifest.write_text(json.dumps(data))
        self.assertRaisesRegex(ValueError,'Display claim drift',validate,self.root)

    def test_changed_display_denominator_or_unmapped_metric_fails(self):
        path=self.root/'docs/research-progress.json';data=json.loads(path.read_text());data['baseline_comparison']['denominator']+=1;path.write_text(json.dumps(data))
        self.assertRaisesRegex(ValueError,'Display claim drift',validate,self.root)
        data['baseline_comparison']['denominator']-=1;data['new_unsupported_rate']=0.9;path.write_text(json.dumps(data))
        self.assertRaisesRegex(ValueError,'Unmapped',validate,self.root)

    def test_numeric_prose_cannot_drift_from_evidence(self):
        path=self.root/'docs/research-progress.json';data=json.loads(path.read_text());data['experiments'][2]['finding']=data['experiments'][2]['finding'].replace('82.31','99.99');path.write_text(json.dumps(data))
        self.assertRaisesRegex(ValueError,'Display claim drift',validate,self.root)


if __name__=='__main__':unittest.main()
