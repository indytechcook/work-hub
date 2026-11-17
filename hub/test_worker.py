import tempfile
import time
import unittest
from pathlib import Path
from store import Store
from worker import budget,reserve

class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'work.db');self.now=time.time()
        self.s.ingest('gmail',[dict(external_id='fixture',title='Fixture',text='Fixture',version='1')],scope='test')
        self.policy={'max_analyses_per_day':120,'catch_up':{'max_analyses_per_day':1200,'through_received_at':self.now+1,'expires_at':self.now+100}}
    def tearDown(self):self.s.close();self.temp.cleanup()
    def test_boost_ends_on_expiry_even_with_backlog(self):
        self.assertEqual(budget(self.s,self.policy,self.now),(1200,True))
        self.assertEqual(budget(self.s,self.policy,self.now+101),(120,False))
    def test_new_traffic_does_not_extend_initial_catch_up(self):
        with self.s.db:self.s.db.execute("UPDATE events SET analysis_state='done'")
        self.s.ingest('gmail',[dict(external_id='new',title='New',text='New',version='1')],scope='test')
        with self.s.db:self.s.db.execute("UPDATE events SET received_at=? WHERE analysis_state='pending'",(self.now+2,))
        self.assertEqual(budget(self.s,self.policy,self.now+3),(120,False))
    def test_limit_reservation_precedes_provider_call(self):
        self.policy['catch_up']['max_analyses_per_day']=2
        self.assertTrue(reserve(self.s,self.policy,self.now)['reserved'])
        self.assertTrue(reserve(self.s,self.policy,self.now)['reserved'])
        self.assertEqual(reserve(self.s,self.policy,self.now),{'state':'daily_limit','limit':2})
