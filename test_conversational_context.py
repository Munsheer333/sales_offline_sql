import unittest
import uuid
from core.conversational_context import resolve_conversational_query, clear_session_context, get_session_history

class ConversationalContextTests(unittest.TestCase):
    def setUp(self):
        self.session_id = str(uuid.uuid4())

    def tearDown(self):
        clear_session_context(self.session_id)

    def test_single_query_is_preserved(self):
        q1 = "Top 10 HPCL zones"
        res1 = resolve_conversational_query(q1, self.session_id)
        self.assertEqual(res1, q1)
        self.assertEqual(get_session_history(self.session_id), [q1])

    def test_refinement_appends_correctly(self):
        q1 = "Top 10 HPCL zones"
        resolve_conversational_query(q1, self.session_id)

        q2 = "Only South"
        res2 = resolve_conversational_query(q2, self.session_id)
        self.assertEqual(res2, "Top 10 HPCL zones Only South")

    def test_comparison_rewrites_previous_query(self):
        q1 = "Top 10 HPCL zones"
        resolve_conversational_query(q1, self.session_id)

        q2 = "Only South"
        resolve_conversational_query(q2, self.session_id)

        q3 = "Compare with BPCL"
        res3 = resolve_conversational_query(q3, self.session_id)
        self.assertEqual(res3, "Compare HPCL with BPCL Top 10 zones Only South")

    def test_zone_replacement(self):
        q1 = "Show sales in North zone"
        resolve_conversational_query(q1, self.session_id)

        q2 = "Only South"
        res2 = resolve_conversational_query(q2, self.session_id)
        self.assertEqual(res2, "Show sales in South zone")

    def test_new_independent_query_resets_history(self):
        q1 = "What is the sales for HPCL in North zone?"
        resolve_conversational_query(q1, self.session_id)

        # Complete sentence starting with a query prefix should reset
        q2 = "What are the total actual sales for Lubes SBU?"
        res2 = resolve_conversational_query(q2, self.session_id)
        self.assertEqual(res2, q2)
        self.assertEqual(get_session_history(self.session_id), [q2])

    def test_growth_followup_preserves_context(self):
        q1 = "Top 10 HPCL zones"
        resolve_conversational_query(q1, self.session_id)

        q2 = "Only South"
        resolve_conversational_query(q2, self.session_id)

        q3 = "Compare with BPCL"
        resolve_conversational_query(q3, self.session_id)

        q4 = "Show growth"
        res4 = resolve_conversational_query(q4, self.session_id)
        self.assertEqual(res4, "Compare HPCL with BPCL Top 10 zones Only South Show growth")

if __name__ == "__main__":
    unittest.main()
