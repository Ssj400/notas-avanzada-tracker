import unittest

from rankings import MY_ID, display_name, rank_students, validate_aliases, validate_student_id


class RankingTests(unittest.TestCase):
    def test_competition_ties_and_stable_id_order(self):
        students = [{"id": sid, "grades": {"final": value}} for sid, value in [("55555", 5), ("33333", 6), ("11111", 7), ("22222", 6)]]
        ranked = rank_students(students, "final")
        self.assertEqual([r["rank"] for r in ranked], [1, 2, 2, 4])
        self.assertEqual([r["student"]["id"] for r in ranked], ["11111", "22222", "33333", "55555"])

    def test_zero_counts_but_null_invalid_and_nan_do_not(self):
        students = [{"id": str(10000 + i), "grades": {"ac1": v}} for i, v in enumerate([None, 0, 2, float("nan"), "4", True])]
        ranked = rank_students(students, "ac1")
        self.assertEqual([r["value"] for r in ranked], [2, 0])
        self.assertEqual(rank_students(students, "missing"), [])

    def test_compare_values_not_display_rounding(self):
        students = [{"id": "11111", "grades": {"final": 5.151}}, {"id": "22222", "grades": {"final": 5.152}}]
        self.assertEqual([r["rank"] for r in rank_students(students, "final")], [1, 2])

    def test_empty_list(self):
        self.assertEqual(rank_students([], "final"), [])

    def test_rank_function_does_not_reorder_input(self):
        students = [{"id": "11111", "grades": {"final": 2}}, {"id": "22222", "grades": {"final": 6}}]
        result = rank_students(students, "final")
        self.assertEqual([s["id"] for s in students], ["11111", "22222"])
        self.assertEqual(result[0]["student"]["id"], "22222")


class AliasTests(unittest.TestCase):
    def test_profile_id_validation(self):
        self.assertEqual(validate_student_id(" 26663279 "), MY_ID)
        self.assertEqual(validate_student_id("2666237j"), "2666237J")
        for value in (None, 12345, "", "bad!", "1" * 21, "123\n456"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_student_id(value)

    def test_default_and_custom_aliases(self):
        self.assertEqual(display_name(MY_ID, {}), "José Garrillo")
        self.assertEqual(display_name("12345678", {}), "12345678")
        aliases = validate_aliases({MY_ID: "  José Garrillo  ", "26666237J": "Ana Muñoz"})
        self.assertEqual(aliases[MY_ID], "José Garrillo")
        self.assertEqual(display_name("26666237J", aliases), "Ana Muñoz")

    def test_reject_invalid_imports(self):
        bad = [[], None, {"bad!": "Ana"}, {MY_ID: 1}, {MY_ID: " "}, {MY_ID: "A" * 81}, {MY_ID: "Ana\nMuñoz"}, {MY_ID: "A\u202eB"}, {str(i): "Ana" for i in range(10000, 12001)}]
        for value in bad:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                validate_aliases(value)


if __name__ == "__main__":
    unittest.main()
