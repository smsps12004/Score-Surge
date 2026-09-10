"""Regression tests for the September trust review. No network or live accounts."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent

class TrustRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (ROOT / 'app.py').read_text()
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'parse_exam_json')
        ns = {'json': json, 're': re}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app.py', 'exec'), ns)
        cls.parse = staticmethod(ns['parse_exam_json'])

    def question(self, key):
        return json.dumps([dict(question='Which option?', answer_a='Alpha', answer_b='Bravo',
                              answer_c='Charlie', answer_d='Delta', correct_answer=key)])

    def test_malformed_answer_keys_are_rejected(self):
        for key in ['Answer B', 'A or B', 'B)', 'Correct: D', 'AB', '']:
            with self.subTest(key=key):
                self.assertEqual(self.parse(self.question(key)), [])

    def test_exact_normalized_keys_are_preserved(self):
        for key in ['A', ' b ', 'C', 'd']:
            with self.subTest(key=key):
                self.assertEqual(self.parse(self.question(key))[0]['correct_answer'], key.strip().upper())

    def test_short_and_overlapping_series_terminate(self):
        # Child timeout ensures a reintroduced infinite loop fails, never hangs CI.
        code = '''
import sqlite3
import corpus
con = sqlite3.connect(':memory:')
con.execute('CREATE TABLE pages(article TEXT, is_current INTEGER, cancelled INTEGER)')
con.executemany('INSERT INTO pages VALUES (?,1,0)', [('1430-010',), ('1430-020',), ('1440-010',)])
corpus._connect = lambda: con
corpus.build_source_block = lambda numbers, **kw: ('source', numbers)
assert corpus.get_series_grounding('MILPERSMAN 1430 series')[1] == ['1430-010', '1430-020']
assert corpus.get_series_grounding('MILPERSMAN 1430 series, MILPERSMAN 1430-010')[1] == ['1430-010', '1430-020']
assert corpus.get_series_grounding('MILPERSMAN 1430 series, 1440 series', max_articles=2)[1] == ['1430-010', '1440-010']
assert corpus.get_series_grounding('MILPERSMAN 1430 series', max_articles=0) == ('', [])
assert corpus.get_series_grounding('MILPERSMAN 1430 series', max_articles=-1) == ('', [])
assert corpus.get_series_grounding('MILPERSMAN 9999 series')[1] == []
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, timeout=10, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_near_mismatch_is_not_claimed_as_matching(self):
        from run_checks import load_logic
        logic = load_logic()
        values = dict(exam_score=62.0, pma=4.06, tir=3.5, awards=4., education=4., pna=6.)
        self.assertFalse(logic['reading_reconciles']('E6', values, 'YOUR FINAL MULTIPLE SCORE 139.00')[0])
        self.assertTrue(logic['reading_reconciles']('E6', values, 'YOUR FINAL MULTIPLE SCORE 138.50')[0])

if __name__ == '__main__':
    unittest.main()
