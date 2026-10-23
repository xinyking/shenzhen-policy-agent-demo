import unittest
from datetime import date

from scripts.refresh_employment import age_status
from app.knowledge.domains import route


class EmploymentRefreshTests(unittest.TestCase):
    def test_cutoff_and_future_dates_are_not_recent(self):
        cutoff, today = date(2024, 10, 18), date(2026, 10, 18)
        for published, expected in [('2024-10-17 12:00', 'old'),
                ('2024-10-18', 'recent'), ('2026-10-18', 'recent'),
                ('2026-10-19', 'future'), (None, 'unknown_date'),
                ('2026-99-99', 'unknown_date')]:
            with self.subTest(published=published):
                self.assertEqual(age_status(published, cutoff, today), expected)

    def test_recruitment_questions_route_to_employment(self):
        for question in ('有哪些招聘会', '有没有校园双选会', '招募公告', '实习岗位'):
            self.assertIn('employment', [item['id'] for item in route(question)['domains']])
