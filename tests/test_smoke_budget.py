from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from back.http_client import ClientError
from scripts.smoke_jev import AttemptBudget


class SmokeBudgetTests(unittest.TestCase):
    def test_quota_counts_concurrent_attempts_and_survives_new_instances(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'quota.json'
            def consume(_):
                try:
                    AttemptBudget(path).consume()
                    return True
                except ClientError:
                    return False
            with ThreadPoolExecutor(max_workers=6) as pool:
                permitted = list(pool.map(consume, range(30)))
            self.assertEqual(sum(permitted), 20)
            self.assertEqual(AttemptBudget(path).used(), 20)
            self.assertFalse(consume(None))
