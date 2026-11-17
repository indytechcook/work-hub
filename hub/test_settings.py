from test_support import isolate_storage
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import settings
from analyzer_runtime import settings as analyzer_settings
from install_local import plans

class SetupPrivacyTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)

        self.temp=tempfile.TemporaryDirectory();self.state=Path(self.temp.name)
        self.patch=patch.object(settings,'STATE',self.state);self.patch.start()
    def tearDown(self):self.patch.stop();self.temp.cleanup()
    def configure(self,value):
        (self.state/'settings.json').write_text(json.dumps({'config':value}))
    def test_fresh_setup_collects_no_external_sources(self):
        self.assertEqual(settings.selected_sources(),[])
    def test_provider_is_required_for_background_work(self):
        with self.assertRaises(ValueError):analyzer_settings(self.state)
    def test_unknown_and_duplicate_sources_fail(self):
        for sources in (['gmail','gmail'],['all'],[{}],'gmail'):
            self.configure({'sources':sources})
            with self.assertRaises(ValueError):settings.selected_sources()
    def test_source_account_must_be_configured(self):
        for fn in (settings.google_account,settings.slack,settings.betterstack,settings.identity):
            with self.assertRaises(ValueError):fn()
    def test_account_and_timezone_are_from_private_config(self):
        self.configure({'identity':{'name':'Sam Example','email':'sam@example.com','timezone':'Pacific/Honolulu'},'google':{'account':'Sam@Example.com'}})
        self.assertEqual(settings.identity()['name'],'Sam Example')
        self.assertEqual(str(settings.timezone()),'Pacific/Honolulu')
        self.assertEqual(settings.google_account(),'sam@example.com')
    def test_slack_workspace_url_cannot_replace_origin(self):
        for url in ('http://example.slack.com','https://example.slack.com.evil.test','https://user:pass@example.slack.com','https://example.slack.com/other','https://example.slack.com?next=evil'):
            self.configure({'slack':{'team_id':'T123456','workspace_url':url}})
            with self.assertRaises(ValueError):settings.slack()
    def test_slack_workspace_origin_and_app_settings_are_respected(self):
        self.configure({'slack':{'team_id':'T123456','workspace_url':'https://example.slack.com/'},'betterstack':{'application_id':42,'environment':'staging'}})
        self.assertEqual(settings.slack()['workspace_url'],'https://example.slack.com')
        self.assertEqual(settings.betterstack(),{'application_id':42,'environment':'staging'})
    def test_bb_only_install_has_no_collector_or_review_job(self):
        self.assertEqual(set(plans({}, {}, [])),{'analyze'})
    def test_collector_job_contains_exact_selected_sources(self):
        jobs=plans({}, {}, ['github','linear'])
        self.assertEqual(jobs['collect']['ProgramArguments'][-2:],['github','linear'])
        self.assertEqual(set(jobs),{'collect','analyze'})
    def test_unconfigured_google_does_not_start_oauth(self):
        self.configure({'sources':['google']})
        with self.assertRaises(ValueError):settings.google_account()
