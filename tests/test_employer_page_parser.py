from unittest import TestCase

from freelancer_monitor.freelancer import (
    EmployerPageParseError,
    _extract_client_section,
    parse_employer_page,
)


PAGE = """
<html><body>
  <app-project-view-logged-out-client-info>
    <h3>About the client</h3>
    <div>Houston, United States</div>
    <fl-rating><div>5.0</div></fl-rating>
    <fl-review-count><span>2</span></fl-review-count>
    <p>Member since May 3, 2026</p>
    <h3>Client Verification</h3>
    <fl-icon aria-label="Deposit made"><span data-color="success"></span></fl-icon>
    <fl-icon aria-label="Profile complete"><span data-color="success"></span></fl-icon>
    <fl-icon aria-label="Payment verified"><span data-color="foreground"></span></fl-icon>
  </app-project-view-logged-out-client-info>
</body></html>
"""


class EmployerPageParserTests(TestCase):
    def test_parses_public_trust_information_and_icon_state(self):
        employer = parse_employer_page(PAGE)
        self.assertEqual(employer.country, "United States")
        self.assertEqual(employer.rating, 5)
        self.assertEqual(employer.review_count, 2)
        self.assertEqual(employer.member_since.year, 2026)
        self.assertTrue(employer.identity_verified)
        self.assertTrue(employer.deposit_made)
        self.assertFalse(employer.payment_verified)

    def test_missing_client_section_is_not_silently_scored(self):
        with self.assertRaises(EmployerPageParseError):
            parse_employer_page("<html><body>No client here</body></html>")

    def test_large_page_is_reduced_to_client_section_before_parsing(self):
        large_page = ("<div>unrelated</div>" * 50_000) + PAGE + ("<footer>x</footer>" * 50_000)
        section = _extract_client_section(large_page)
        self.assertLess(len(section), 2_000)
        self.assertEqual(parse_employer_page(large_page).country, "United States")
