import unittest
from unittest.mock import patch
from references import SourceGone
import slack


class SlackClientTests(unittest.TestCase):
    def test_missing_message_is_permanent(self):
        for code in ('thread_not_found','message_not_found'):
            with self.assertRaises(SourceGone):slack.raise_failure(['execution_failed: '+code])
    def test_other_failures_stay_retryable(self):
        with self.assertRaises(RuntimeError) as caught:slack.raise_failure(['execution_failed: ratelimited'])
        self.assertNotIsInstance(caught.exception,SourceGone)
    def test_call_unwraps_missing_message_from_task_groups(self):
        async def invoke(tool,args):
            raise ExceptionGroup('outer',[ExceptionGroup('inner',[SourceGone('gone')])])
        with patch.object(slack,'invoke',invoke):
            with self.assertRaises(SourceGone):slack.call('slack_read_thread',{})
    def test_call_keeps_other_task_group_failures(self):
        async def invoke(tool,args):
            raise ExceptionGroup('outer',[RuntimeError('Slack MCP read failed')])
        with patch.object(slack,'invoke',invoke):
            with self.assertRaises(ExceptionGroup):slack.call('slack_read_thread',{})


if __name__=='__main__':
    unittest.main()
