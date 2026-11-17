from test_support import isolate_storage
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from store import Store
from collect import Collector,receipt,failure_detail

class CollectorTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'work.db');self.c=Collector(self.s);self.state_patch=patch("collect.STATE",Path(self.temp.name));self.state_patch.start()
    def tearDown(self):self.state_patch.stop();self.s.close();self.temp.cleanup()
    def test_source_failure_does_not_advance_checkpoint(self):
        self.s.ingest('gmail',[],checkpoint={'through':123},scope='initial')
        with patch.object(self.c,'gmail',side_effect=RuntimeError('failed')):self.assertEqual(self.c.collect('gmail')['state'],'error')
        self.assertEqual(self.c.checkpoint('gmail'),{'through':123})
    def test_partial_batches_keep_receipts_but_not_checkpoint(self):
        self.c.save('github',[receipt('a','PR','body')],'partial',checkpoint=99,complete=False)
        self.assertIsNone(self.c.checkpoint('github'));self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)
    def test_metadata_does_not_become_fictional_obligation(self):
        self.c.save('drive',[receipt('a','filename','metadata')],'metadata only')
        self.assertIsNone(self.s.claim_analysis());self.assertEqual(self.s.snapshot()['work'],[])
    def test_nested_source_failure_is_actionable_without_private_details(self):
        class SourceSignInRequired(RuntimeError):pass
        class Group(Exception):
            def __init__(self,*children):self.exceptions=children
        error=Group(Group(SourceSignInRequired('private token and source payload')))
        self.s.ingest('meetings',[],checkpoint={'through':123},scope='initial')
        with patch.object(self.c,'meetings',side_effect=error):result=self.c.collect('meetings')
        self.assertEqual(result['detail'],'Source sign-in needs renewal; previous checkpoint retained')
        self.assertEqual(self.c.checkpoint('meetings'),{'through':123})
        self.assertNotIn('private',failure_detail(RuntimeError('private payload')))

    def test_meeting_xml_cannot_resolve_entities(self):
        from defusedxml.common import DTDForbidden
        from collect import ET
        with self.assertRaises(DTDForbidden):
            ET.fromstring('<!DOCTYPE root [<!ENTITY leak SYSTEM "file:///not-a-real-secret">]><root>&leak;</root>',forbid_dtd=True,forbid_entities=True,forbid_external=True)
