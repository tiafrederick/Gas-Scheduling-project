"""Citation gate tests (OI-5.3; OI doc §1.2).

The gate is the trust boundary for any future LLM narration, so it earns the
same planted-violation discipline as the extraction span gate: prove it strips
uncited claims, fails the whole narration on an invented uid, and passes clean
fully-cited prose untouched.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nge.citegate import cited_uids, verify


class TestCitedUids(unittest.TestCase):
    def test_comma_split_preserves_colon_uids(self):
        # a typed uid like point:C000307:4208 must survive intact
        self.assertEqual(
            cited_uids("Curtailments at 4208R [evt:ab12, point:C000307:4208]."),
            ["evt:ab12", "point:C000307:4208"])

    def test_no_bracket_is_no_citation(self):
        self.assertEqual(cited_uids("A bare claim with no evidence."), [])


class TestGatePassesCleanProse(unittest.TestCase):
    def test_all_sentences_cited_and_known_kept(self):
        bundle = {"evt:a", "imp:b", "point:c"}
        prose = ("Corinth remains in force majeure [evt:a]. "
                 "SESH receipts are exposed at 4208R [imp:b, point:c].")
        r = verify(prose, bundle)
        self.assertTrue(r.ok)
        self.assertEqual(r.n_kept, 2)
        self.assertEqual(r.n_stripped, 0)
        self.assertIn("Corinth", r.text)
        self.assertIn("SESH receipts", r.text)


class TestGateStripsUncited(unittest.TestCase):
    def test_uncited_sentence_stripped_and_counted(self):
        bundle = {"evt:a"}
        prose = ("Corinth remains in force majeure [evt:a]. "
                 "Prices will spike tomorrow.")     # planted: no citation
        r = verify(prose, bundle)
        self.assertTrue(r.ok)                        # no UNKNOWN uid -> still usable
        self.assertEqual(r.n_kept, 1)
        self.assertEqual(r.n_stripped, 1)
        self.assertIn("Prices will spike tomorrow.", r.stripped)
        self.assertNotIn("spike", r.text)


class TestGateFailsOnUnknownUid(unittest.TestCase):
    def test_invented_uid_fails_whole_narration(self):
        bundle = {"evt:a"}
        prose = ("Corinth remains in force majeure [evt:a]. "
                 "Egan is also down [evt:fabricated].")   # planted: unknown uid
        r = verify(prose, bundle)
        self.assertFalse(r.ok)                       # whole narration rejected
        self.assertEqual(r.text, "")                 # caller must use template
        self.assertIn("evt:fabricated", r.unknown_uids)

    def test_one_bad_uid_poisons_even_otherwise_valid_sentence(self):
        bundle = {"evt:a", "evt:b"}
        prose = "A mixed claim [evt:b, evt:ghost]."
        r = verify(prose, bundle)
        self.assertFalse(r.ok)
        self.assertIn("evt:ghost", r.unknown_uids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
