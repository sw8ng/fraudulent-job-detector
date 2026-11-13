import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from bounded_assessment import run_bounded_assessment, partial_report
from compare_models import evaluation_cases, find_saved_run, main
from assess_job import assess_job


class DeadlineTests(unittest.TestCase):
    job = {'description':'An engineering role.', 'application_url':'https://example.com/apply'}

    def test_checkpoint_keeps_content_when_final_model_call_stops(self):
        client = Mock()
        client.chat.side_effect = [SimpleNamespace(done_reason='stop', message=SimpleNamespace(content=json.dumps({
            'observations':[], 'claims_to_verify':[], 'limitations':[]}))), RuntimeError('Stopped call')]
        with tempfile.TemporaryDirectory() as folder:
            checkpoint = Path(folder)/'progress.json'
            with self.assertRaisesRegex(RuntimeError,'Stopped call'):
                assess_job({'description':'An engineering role.'},model='test-model',client=client,progress_file=checkpoint)
            progress = json.loads(checkpoint.read_text(encoding='utf-8'))
            self.assertEqual(progress['content_analysis']['status'],'completed')
            self.assertEqual(progress['performance']['model_calls'][0]['status'],'completed')
            self.assertEqual(progress['performance']['model_calls'][-1]['status'],'failed')
            self.assertFalse(Path(str(checkpoint)+'.tmp').exists())

    def test_timeout_returns_completed_evidence_without_another_model_call(self):
        def timeout(command, **kwargs):
            progress_file = Path(command[command.index('--progress-file') + 1])
            progress_file.write_text(json.dumps({'model':'test-model', 'tool_calls':[
                {'tool':'inspect_application_url','evidence':{'redirect_lookup_success':True,
                    'final_url':'https://example.com/form','http_status':200,'redirects':[]}}
            ]}), encoding='utf-8')
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        with patch('bounded_assessment.subprocess.run', side_effect=timeout) as worker:
            result = run_bounded_assessment(self.job, model='test-model', max_seconds=2)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(result['execution_status'], 'timed_out')
        self.assertEqual(result['investigation']['tool_calls'][0]['evidence']['http_status'], 200)
        self.assertTrue(any(source['type']=='url_inspection' for source in result['evidence_sources']))
        self.assertIn('did not finish', result['assessment']['summary'])

    def test_real_worker_is_killed_at_deadline_without_running_a_model(self):
        started = time.perf_counter()
        with patch.dict(os.environ, {'OLLAMA_HOST':'http://127.0.0.1:1'}):
            result = run_bounded_assessment(self.job, model='unused-test-model', max_seconds=0.01)
        self.assertLess(time.perf_counter()-started, 5)
        self.assertEqual(result['execution_status'], 'timed_out')

    def test_successful_worker_result_is_not_replaced_by_partial_summary(self):
        expected = {'assessment':{'risk_level':'low','summary':'Test completed.'}}
        def complete(command, **kwargs):
            output = Path(command[command.index('--output') + 1])
            output.write_text(json.dumps(expected), encoding='utf-8')
            return SimpleNamespace(returncode=0, stderr='')
        with patch('bounded_assessment.subprocess.run', side_effect=complete):
            result = run_bounded_assessment(self.job, model='test-model', max_seconds=2)
        self.assertEqual(result, expected)

    def test_timed_out_results_are_not_automatically_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            input_file = folder/'input.json'
            input_file.write_text(json.dumps(self.job), encoding='utf-8')
            case = evaluation_cases([input_file])[0]
            report = partial_report(self.job,'test-model',{},2,'timed_out','Time limit reached.')
            (folder/'partial.json').write_text(json.dumps(report), encoding='utf-8')
            previous = {'cases':[case], 'runs':[{'source':case['source'],'case':case['name'],
                'model':'test-model','repetition':1,'status':'timed_out','report':'partial.json'}]}
            saved = find_saved_run(previous,case,'test-model',1,folder)
            self.assertIsNotNone(saved)
            self.assertEqual(saved['status'],'timed_out')
            self.assertTrue(saved['reused'])

    def test_expired_batch_budget_does_not_start_queued_work(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            input_file = folder/'input.json'
            input_file.write_text(json.dumps(self.job), encoding='utf-8')
            output = folder/'results'
            argv = ['compare_models.py','--models','test-model','--inputs',str(input_file),
                    '--output',str(output),'--max-total-seconds','0.1']
            with patch.object(sys,'argv',argv), patch('compare_models.Client') as client, \
                 patch('compare_models.time.perf_counter', side_effect=[0,1]), patch('compare_models.run_case') as runner:
                client.return_value.list.return_value = SimpleNamespace(models=[SimpleNamespace(model='test-model')])
                main()
            runner.assert_not_called()
            result = json.loads((output/'comparison.json').read_text(encoding='utf-8'))['runs'][0]
            self.assertEqual(result['status'],'not_run')
            self.assertIsNone(result['seconds'])


    def test_remaining_batch_time_does_not_shorten_a_case(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            input_file = folder/'input.json'
            input_file.write_text(json.dumps(self.job), encoding='utf-8')
            output = folder/'results'
            argv = ['compare_models.py','--models','future-model','--inputs',str(input_file),
                    '--output',str(output),'--max-seconds','180','--max-total-seconds','200']
            with patch.object(sys,'argv',argv), patch('compare_models.Client') as client, \
                 patch('compare_models.time.perf_counter', side_effect=[0,30]), patch('compare_models.run_case') as runner:
                client.return_value.list.return_value = SimpleNamespace(models=[SimpleNamespace(model='future-model')])
                main()
            runner.assert_not_called()
            row = json.loads((output/'comparison.json').read_text(encoding='utf-8'))['runs'][0]
            self.assertEqual(row['status'],'not_run')
            self.assertIn('full assessment allowance', row['error'])
