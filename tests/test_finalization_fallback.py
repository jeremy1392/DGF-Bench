"""Structured finalization falls back to plain JSON when a pinned provider lacks structured outputs."""
import unittest

from dgf_bench.openrouter_eval.agent_runner import _finalization_request
from dgf_bench.openrouter_eval.openrouter_client import BudgetStopped, OpenRouterError


class Client:
    def __init__(self, error):
        self.error, self.bodies = error, []

    def chat(self, body):
        self.bodies.append(body)
        if self.error and 'response_format' in body:
            raise self.error
        return {'choices': [{'message': {'content': '{}'}}]}


class FinalizationFallbackTests(unittest.TestCase):
    def test_no_endpoint_for_structured_output_retries_without_it(self):
        client = Client(OpenRouterError('HTTP 404: No endpoints found for z-ai/glm-5.3. Filter by Parameters removed inceptron/fp4'))
        response, structured = _finalization_request(client, {'model': 'm', 'response_format': {'type': 'json_schema'}})
        self.assertFalse(structured)
        self.assertEqual(len(client.bodies), 2)
        self.assertNotIn('response_format', client.bodies[1])
        self.assertIn('choices', response)

    def test_other_errors_and_budget_stops_are_not_retried(self):
        for error in (OpenRouterError('HTTP 500: upstream'), BudgetStopped('No endpoints found')):
            client = Client(error)
            with self.assertRaises(OpenRouterError):
                _finalization_request(client, {'model': 'm', 'response_format': {'type': 'json_schema'}})
            self.assertEqual(len(client.bodies), 1)

    def test_structured_call_succeeds_unchanged(self):
        client = Client(None)
        _, structured = _finalization_request(client, {'model': 'm', 'response_format': {'type': 'json_schema'}})
        self.assertTrue(structured)
        self.assertEqual(len(client.bodies), 1)


if __name__ == '__main__':
    unittest.main()
