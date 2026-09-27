"""Offline transport failures: retry the request, never select a better answer."""
import contextlib
import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from dgf_bench.openrouter_eval import openrouter_client as transport
from dgf_bench.openrouter_eval.benchmark_runner import BudgetedClient, CostBudget


def http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers['Retry-After'] = retry_after
    return HTTPError('https://example.invalid', code, 'test', headers,
                     io.BytesIO(b'{"error":"temporary"}'))


def response(payload=None):
    result = MagicMock()
    result.__enter__.return_value.read.return_value = json.dumps(
        payload if payload is not None else {'usage': {'cost': .03}, 'choices': []}
    ).encode()
    return result


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(transport, 'load_dotenv'))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.sleep = self.stack.enter_context(patch.object(transport.time, 'sleep'))
        self.stack.enter_context(patch.object(transport.random, 'random', return_value=0))
        self.urlopen = self.stack.enter_context(patch.object(transport.request, 'urlopen'))

    def test_rate_limit_retries_identical_request_and_charges_success_once(self):
        self.urlopen.side_effect = [http_error(429, '9'), response()]
        client = transport.OpenRouterClient('unused', retries=2)
        budget = CostBudget(1)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / 'usage.jsonl'
            wrapped = BudgetedClient(client, budget, 'job', ledger)
            wrapped.chat({'model': 'fake/model', 'messages': []})
            self.assertEqual(len(ledger.read_text().splitlines()), 1)
        self.assertAlmostEqual(budget.spent, .03)
        calls = self.urlopen.call_args_list
        self.assertEqual(calls[0].args[0].data, calls[1].args[0].data)
        self.assertEqual(sum(c.args[0] for c in self.sleep.call_args_list), 9)
        self.assertEqual(client.stats_snapshot()['retries'], 1)
        self.assertEqual(client.stats_snapshot()['rate_limits'], 1)

    def test_retry_exhaustion_is_bounded(self):
        self.urlopen.side_effect = [http_error(429) for _ in range(3)]
        client = transport.OpenRouterClient('unused', retries=2)
        with self.assertRaisesRegex(transport.OpenRouterError, 'HTTP 429'):
            client.chat({'model': 'fake/model'})
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual(sum(c.args[0] for c in self.sleep.call_args_list), 6)

    def test_permanent_errors_and_disabled_retries_do_not_retry(self):
        for code, retries in [(400, 8), (401, 8), (403, 8), (402, 8), (429, 0)]:
            with self.subTest(code=code, retries=retries):
                self.urlopen.reset_mock()
                self.urlopen.side_effect = http_error(code)
                with self.assertRaises(transport.OpenRouterError):
                    transport.OpenRouterClient('unused', retries=retries).chat({})
                self.assertEqual(self.urlopen.call_count, 1)
        self.sleep.assert_not_called()

    def test_transient_http_and_network_errors_retry(self):
        for failure in [http_error(408), http_error(409), http_error(500), http_error(502),
                        http_error(503), http_error(504), URLError('offline'), TimeoutError()]:
            with self.subTest(failure=type(failure).__name__):
                self.urlopen.reset_mock()
                self.urlopen.side_effect = [failure, response()]
                transport.OpenRouterClient('unused', retries=1).chat({})
                self.assertEqual(self.urlopen.call_count, 2)

    def test_budget_spent_by_another_worker_stops_waiting_retry(self):
        budget = CostBudget(1)
        wrapped = BudgetedClient(transport.OpenRouterClient('unused'), budget, 'job')
        self.urlopen.side_effect = http_error(429, '10')
        self.sleep.side_effect = lambda _: budget.charge('other', 1)
        with self.assertRaises(transport.BudgetStopped):
            wrapped.chat({})
        self.assertEqual(self.urlopen.call_count, 1)
        self.assertEqual(self.sleep.call_count, 1)

    def test_unknown_cost_from_another_worker_stops_retry(self):
        budget = CostBudget(1)
        wrapped = BudgetedClient(transport.OpenRouterClient('unused'), budget, 'job')
        self.urlopen.side_effect = http_error(503)
        self.sleep.side_effect = lambda _: budget.mark_unknown(1)
        with self.assertRaises(transport.BudgetStopped):
            wrapped.chat({})
        self.assertEqual(self.urlopen.call_count, 1)

    def test_retry_after_date_and_invalid_headers(self):
        with patch.object(transport.time, 'time', return_value=0):
            self.assertEqual(transport.retry_after_seconds('Thu, 01 Jan 1970 00:00:10 GMT'), 10)
        for value in [None, 'invalid', 'nan', 'inf']:
            self.assertIsNone(transport.retry_after_seconds(value))
        self.assertEqual(transport.retry_after_seconds('-1'), 0)

    def test_long_server_wait_is_not_shortened(self):
        self.urlopen.side_effect = http_error(429, '301')
        with self.assertRaisesRegex(transport.OpenRouterError, 'exceeds 300'):
            transport.OpenRouterClient('unused').chat({})
        self.assertEqual(self.urlopen.call_count, 1)
        self.sleep.assert_not_called()

    def test_invalid_json_and_valid_low_quality_answers_are_not_retried(self):
        invalid = response()
        invalid.__enter__.return_value.read.return_value = b'not-json'
        self.urlopen.side_effect = [invalid, response({'answer': 'wrong'})]
        client = transport.OpenRouterClient('unused')
        with self.assertRaisesRegex(transport.OpenRouterError, 'invalid JSON'):
            client.chat({})
        self.assertEqual(self.urlopen.call_count, 1)
        self.assertEqual(client.chat({}), {'answer': 'wrong'})
        self.assertEqual(self.urlopen.call_count, 2)
        self.sleep.assert_not_called()

    def test_invalid_retry_count_rejected(self):
        for value in [-1, True, 1.5]:
            with self.assertRaises(ValueError):
                transport.OpenRouterClient('unused', retries=value)

    def test_unreadable_response_records_unknown_billing_and_blocks_next_call(self):
        invalid=response()
        invalid.__enter__.return_value.read.return_value=b'not-json'
        self.urlopen.return_value=invalid
        budget=CostBudget(1)
        with tempfile.TemporaryDirectory() as directory:
            ledger=Path(directory)/'usage.jsonl'
            wrapped=BudgetedClient(transport.OpenRouterClient('unused'),budget,'job',ledger)
            with self.assertRaises(transport.BillingUnknown): wrapped.chat({})
            self.assertEqual(json.loads(ledger.read_text())['unknown_cost_calls'],1)
            with self.assertRaises(transport.BudgetStopped): wrapped.chat({})
        self.assertEqual(self.urlopen.call_count,1)

    def test_malformed_token_metadata_does_not_discard_known_charge(self):
        usage=transport.Usage.from_response({'usage':{'cost':.25,'prompt_tokens':'bad',
                      'completion_tokens_details':[], 'total_tokens':float('inf')}})
        self.assertEqual(usage.cost,.25)
        self.assertEqual(usage.total_tokens,0)
        self.assertEqual(transport.Usage.from_response({'usage':[]}).unknown_cost_calls,1)


if __name__ == '__main__':
    unittest.main()
