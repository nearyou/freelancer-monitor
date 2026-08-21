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
    def test_resolves_all_skill_names_once_and_caches_ids(self):
        response = mock.Mock()
        response.json.return_value = {
            "result": [{"id": 13, "name": "Python"}, {"id": 7, "name": "Java"}]
        }
        session = mock.Mock()
        session.get.return_value = response
        client = FreelancerClient(
            "https://example.test/api/projects/0.1/projects/active/", session=session
        )

        self.assertEqual(client.resolve_skill_ids(("Python", "Java")), (13, 7))
        self.assertEqual(client.resolve_skill_ids(("Python", "Java")), (13, 7))
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(
            session.get.call_args.kwargs["params"],
            {"job_names[]": ["Python", "Java"]},
        )
        self.assertEqual(
            session.get.call_args.args[0], "https://example.test/api/projects/0.1/jobs/"
        )

    def test_discovery_combines_all_filters_and_requests_newest_first(self):
        response = mock.Mock()
        response.json.return_value = {"result": {"projects": []}}
        session = mock.Mock()
        session.get.return_value = response
        client = FreelancerClient("https://example.test/api", session=session)

        client.fetch_active_projects(
            20,
            search_terms=("Python", "API Integration"),
            skill_ids=(6, 7, 6),
            languages=("en", "ja"),
        )

        parameters = session.get.call_args.kwargs["params"]
        self.assertEqual(parameters["query"], "Python API Integration")
        self.assertEqual(parameters["jobs[]"], [6, 7])
        self.assertEqual(parameters["languages[]"], ["en", "ja"])
        self.assertEqual(parameters["sort_field"], "time_updated")
        # Freelancer's active endpoint returns newest first when reverse_sort
        # is omitted; either boolean value currently reverses it to oldest.
        self.assertNotIn("reverse_sort", parameters)

    def test_detail_session_retries_transient_not_found_responses(self):
        client = FreelancerClient("https://example.test/api")
        try:
            retries = client.session.get_adapter("https://").max_retries
            self.assertIn(404, retries.status_forcelist)
        finally:
            client.close()

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
                            "profile_complete": True,
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
        self.assertTrue(project.employer.payment_verified)
        self.assertTrue(project.employer.deposit_made)
        self.assertTrue(project.employer.profile_completed)

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
