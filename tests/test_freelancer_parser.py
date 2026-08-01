from unittest import TestCase
from unittest import mock

from freelancer_monitor.freelancer import FreelancerClient, parse_projects_response
from freelancer_monitor.models import Project


CLIENT_PAGE = b"""
<app-project-view-logged-out-client-info>
  <h3>About the client</h3><div>Tokyo, Japan</div>
  <fl-rating><div>4.5</div></fl-rating>
  <fl-review-count><span>8</span></fl-review-count>
  <p>Member since Jan 1, 2026</p>
  <fl-icon aria-label="Payment verified"><span data-color="success"></span></fl-icon>
</app-project-view-logged-out-client-info>
"""


class FakeResponse:
    def __init__(self, body: bytes):
        self.text = body.decode("utf-8")

    def raise_for_status(self):
        return None


class FreelancerParserTests(TestCase):
    def test_parses_project_and_employer_reputation(self):
        payload = {
            "result": {
                "projects": [
                    {
                        "id": 42,
                        "owner_id": 7,
                        "title": "Python API",
                        "description": "Build it",
                        "seo_url": "python/python-api-42",
                        "jobs": [{"name": "Python"}],
                        "budget": {"minimum": 100, "maximum": 500},
                        "currency": {"code": "USD"},
                        "upgrades": {
                            "featured": True,
                            "sealed": True,
                            "NDA": True,
                            "urgent": True,
                            "recruiter": True,
                            "ip_contract": True,
                        },
                    }
                ],
                "users": {
                    "7": {
                        "registration_date": 1577836800,
                        "status": {
                            "identity_verified": True,
                            "payment_verified": True,
                            "deposit_made": True,
                        },
                        "employer_reputation": {
                            "entire_history": {"overall": 4.8, "reviews": 12}
                        },
                    }
                },
            }
        }
        project = parse_projects_response(payload)[0]
        self.assertEqual(project.id, 42)
        self.assertEqual(project.skills, ("Python",))
        self.assertEqual(
            project.listing_types,
            ("Featured", "Sealed", "NDA", "Urgent", "Recruiter", "IP Agreement"),
        )
        self.assertEqual(project.employer.rating, 4.8)
        self.assertEqual(project.employer.review_count, 12)
        self.assertTrue(project.employer.deposit_made)

    def test_same_employer_is_enriched_only_once_per_poll(self):
        client = FreelancerClient("https://example.test/api", detail_request_delay_seconds=0)
        first = Project(1, "First", "", "https://example.test/1", owner_id=77)
        second = Project(2, "Second", "", "https://example.test/2", owner_id=77)

        with mock.patch.object(
            client.session, "get", return_value=FakeResponse(CLIENT_PAGE)
        ) as mocked_get:
            enriched_first = client.enrich_from_public_page(first)
            enriched_second = client.enrich_from_public_page(second)

        self.assertEqual(mocked_get.call_count, 1)
        self.assertEqual(enriched_first.employer, enriched_second.employer)
