import copy
import json
from pathlib import Path
from unittest import TestCase
from unittest import mock

from freelancer_monitor.freelancer import FreelancerAPIError, FreelancerClient, parse_projects_response


def api_fixture():
    return json.loads((Path(__file__).parent / "fixtures" / "active_projects.json").read_text(encoding="utf-8"))


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
        self.assertEqual(parameters["sort_field"], "submitdate")
        # Freelancer's active endpoint returns newest first when reverse_sort
        # is omitted; either boolean value currently reverses it to oldest.
        self.assertNotIn("reverse_sort", parameters)
        self.assertEqual(parameters["owner_info"], "true")
        self.assertEqual(parameters["upgrade_details"], "true")
        self.assertEqual(parameters["compact"], "true")
        self.assertEqual(parameters["webapp"], "1")
        self.assertEqual(session.get.call_count, 1)

    def test_api_session_retries_transient_errors(self):
        client = FreelancerClient("https://example.test/api")
        try:
            retries = client.session.get_adapter("https://").max_retries
            self.assertIn(429, retries.status_forcelist)
            self.assertIn(503, retries.status_forcelist)
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
        self.assertTrue(project.employer_data_complete)

    def test_live_response_shape_has_inline_owner_without_users_or_owner_id(self):
        projects = parse_projects_response(api_fixture())
        self.assertEqual(len(projects), 2)
        unrated, rated = projects
        self.assertIsNone(unrated.owner_id)
        self.assertTrue(unrated.employer_data_complete)
        self.assertEqual(unrated.employer.review_count, 0)
        self.assertEqual(unrated.employer.rating, 0)
        self.assertEqual(unrated.employer.country, "Japan")
        self.assertTrue(unrated.employer.phone_verified)
        self.assertFalse(unrated.employer.identity_verified)
        self.assertTrue(unrated.employer.email_verified)
        self.assertTrue(rated.employer_data_complete)
        self.assertEqual(rated.employer.review_count, 8)
        self.assertEqual(rated.employer.rating, 5)
        self.assertIsNotNone(rated.employer.member_since)

    def test_payment_and_deposit_are_mapped_without_the_old_html_swap(self):
        payload = api_fixture()
        status = payload["result"]["projects"][0]["owner_info"]["status"]
        for payment, deposit in ((True, False), (False, True)):
            with self.subTest(payment=payment, deposit=deposit):
                status.update(payment_verified=payment, deposit_made=deposit)
                employer = parse_projects_response(payload)[0].employer
                self.assertEqual(employer.payment_verified, payment)
                self.assertEqual(employer.deposit_made, deposit)

    def test_inline_owner_takes_precedence_over_legacy_users(self):
        payload = api_fixture()
        payload["result"]["projects"][0]["owner_id"] = 99
        payload["result"]["users"] = {"99": {"country": {"name": "Wrong country"}}}
        self.assertEqual(parse_projects_response(payload)[0].employer.country, "Japan")

    def test_missing_required_fields_defer_but_optional_verifications_are_unknown(self):
        original = api_fixture()
        for field in ("status", "registration_date", "reputation"):
            payload = copy.deepcopy(original)
            del payload["result"]["projects"][0]["owner_info"][field]
            with self.subTest(field=field):
                self.assertFalse(parse_projects_response(payload)[0].employer_data_complete)
        payload = copy.deepcopy(original)
        payload["result"]["projects"][0]["owner_info"] = None
        payload["result"]["users"] = None
        self.assertFalse(parse_projects_response(payload)[0].employer_data_complete)
        payload = copy.deepcopy(original)
        status = payload["result"]["projects"][0]["owner_info"]["status"]
        del status["identity_verified"]
        project = parse_projects_response(payload)[0]
        self.assertTrue(project.employer_data_complete)
        self.assertIsNone(project.employer.identity_verified)
        del status["payment_verified"]
        self.assertFalse(parse_projects_response(payload)[0].employer_data_complete)

    def test_rated_employer_requires_review_count(self):
        payload = api_fixture()
        del payload["result"]["projects"][1]["owner_info"]["reputation"]["entire_history"]["reviews"]
        self.assertFalse(parse_projects_response(payload)[1].employer_data_complete)

    def test_malformed_trust_numbers_do_not_crash_or_receive_valid_scores(self):
        for value in (None, "invalid", float("nan"), float("inf")):
            with self.subTest(value=value):
                payload = api_fixture()
                payload["result"]["projects"][0]["owner_info"]["reputation"]["entire_history"]["overall"] = value
                self.assertFalse(parse_projects_response(payload)[0].employer_data_complete)
        payload = api_fixture()
        payload["result"]["projects"][0]["owner_info"]["reputation"]["entire_history"]["reviews"] = "invalid"
        self.assertFalse(parse_projects_response(payload)[0].employer_data_complete)

    def test_api_errors_are_not_treated_as_an_empty_successful_poll(self):
        for payload in ({"status": "error"}, {"result": {}}, {"result": {"projects": None}}, []):
            with self.subTest(payload=payload):
                with self.assertRaises(FreelancerAPIError):
                    parse_projects_response(payload)

    def test_full_api_url_preserves_filters_without_duplicate_query_parameters(self):
        session = mock.Mock()
        session.get.return_value.json.return_value = api_fixture()
        client = FreelancerClient(
            "https://example.test/api/projects/0.1/projects/active?limit=20&owner_info=false"
            "&jobs%5B%5D=3&jobs%5B%5D=9&languages%5B%5D=en"
            "&project_types%5B%5D=fixed&max_price=1500",
            session=session,
        )
        projects = client.fetch_active_projects(50)
        self.assertEqual(len(projects), 2)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(session.get.call_args.args[0], "https://example.test/api/projects/0.1/projects/active")
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["limit"], 50)
        self.assertEqual(params["owner_info"], "true")
        self.assertEqual(params["jobs[]"], ["3", "9"])
        self.assertEqual(params["project_types[]"], ["fixed"])
        self.assertEqual(params["max_price"], ["1500"])
        client.fetch_active_projects(20, skill_ids=(13,), languages=("ja",))
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(params["jobs[]"], [13])
        self.assertEqual(params["languages[]"], ["ja"])
        session.get.return_value.json.return_value = {"result": [{"name": "Python", "id": 13}]}
        self.assertEqual(client.resolve_skill_ids(("Python",)), (13,))
        self.assertEqual(session.get.call_args.args[0], "https://example.test/api/projects/0.1/jobs/")
