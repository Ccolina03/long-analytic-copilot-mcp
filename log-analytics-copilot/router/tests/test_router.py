"""
Phase 4 — Ticket Router tests.

Tests
-----
  test_router_extracts_team_field_from_normalized_ticket
  test_router_maps_team_name_to_correct_agent_address
  test_router_returns_400_for_missing_team
  test_router_returns_422_for_unknown_team
  test_router_accepts_mirrormaker_ticket
  test_health_endpoint_returns_known_teams
  test_normalize_github_issue_webhook_preserves_team_label
  test_normalize_jira_webhook_preserves_team_field
  test_malformed_payload_returns_400
  test_payload_missing_team_returns_400_with_clear_error
"""

import pytest
from fastapi.testclient import TestClient

from proto.sme_agents import Ticket
from router.main import (
    AGENT_ADDRESSES,
    app,
    get_agent_address,
    normalize_ticket,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# normalize_ticket unit tests
# ---------------------------------------------------------------------------

class TestNormalizeTicket:
    def test_extracts_team_field_from_raw_json(self):
        payload = {
            "team": "mirrormaker",
            "title": "checkpoint group discovery slow",
            "description": "p99=11s",
        }
        ticket = normalize_ticket(payload)
        assert ticket.team == "mirrormaker"
        assert ticket.title == "checkpoint group discovery slow"

    def test_missing_team_raises_key_error(self):
        with pytest.raises(KeyError):
            normalize_ticket({"title": "no team here"})

    def test_empty_team_raises_key_error(self):
        with pytest.raises(KeyError):
            normalize_ticket({"team": "", "title": "empty team"})

    def test_normalize_github_issue_webhook_preserves_team_label(self):
        payload = {
            "action": "opened",
            "issue": {
                "title": "Failover latency spike",
                "body": "checkpoint group discovery is slow",
                "html_url": "https://github.com/org/repo/issues/1",
                "labels": [
                    {"name": "team: mirrormaker"},
                    {"name": "priority: high"},
                ],
            },
        }
        ticket = normalize_ticket(payload)
        assert ticket.team == "mirrormaker"
        assert ticket.source == "github"

    def test_normalize_jira_webhook_preserves_team_field(self):
        payload = {
            "issue": {
                "self": "https://jira.example.com/rest/api/2/issue/KAFKA-18231",
                "fields": {
                    "summary": "checkpoint group discovery p99 spike",
                    "description": "8-12s on large clusters",
                    "priority": {"name": "High"},
                    "team": {"name": "mirrormaker"},
                },
            },
        }
        ticket = normalize_ticket(payload)
        assert ticket.team == "mirrormaker"
        assert ticket.source == "jira"

    def test_ticket_has_auto_generated_id(self):
        ticket = normalize_ticket({"team": "mirrormaker", "title": "t"})
        assert ticket.ticket_id
        assert len(ticket.ticket_id) > 0


class TestGetAgentAddress:
    def test_maps_mirrormaker_to_correct_address(self):
        addr = get_agent_address("mirrormaker")
        assert addr is not None
        assert "mirrormaker" in addr or "8001" in addr

    def test_maps_consumer_team_to_correct_address(self):
        addr = get_agent_address("group-coordinator")
        assert addr is not None

    def test_returns_none_for_unknown_team(self):
        addr = get_agent_address("nonexistent-team")
        assert addr is None


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_returns_200(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_response_contains_known_teams(self):
        resp = client.get("/health")
        data = resp.json()
        assert "known_teams" in data
        assert "mirrormaker" in data["known_teams"]
        assert "group-coordinator" in data["known_teams"]


class TestSubmitTicket:
    def test_accepts_mirrormaker_ticket_with_202(self):
        resp = client.post("/tickets", json={
            "team": "mirrormaker",
            "title": "checkpoint group discovery slow",
            "description": "p99=11s",
        })
        assert resp.status_code == 202
        data = resp.json()
        assert data["routed_to"] == "mirrormaker"
        assert data["status"] == "accepted"
        assert "ticket_id" in data

    def test_ticket_with_missing_team_returns_400(self):
        resp = client.post("/tickets", json={"title": "no team"})
        assert resp.status_code == 400
        assert "team" in resp.json()["detail"].lower()

    def test_ticket_with_unknown_team_returns_422(self):
        resp = client.post("/tickets", json={
            "team": "totally-unknown-team",
            "title": "nobody owns this",
        })
        assert resp.status_code == 422
        assert "unknown team" in resp.json()["detail"].lower()

    def test_router_routes_to_correct_agent_address(self):
        resp = client.post("/tickets", json={
            "team": "group-coordinator",
            "title": "group state issue",
        })
        assert resp.status_code == 202
        assert resp.json()["routed_to"] == "group-coordinator"

    def test_router_does_no_domain_classification(self):
        """Submitting without a team must ALWAYS fail, even for obvious content."""
        resp = client.post("/tickets", json={
            "title": "GroupCoordinator rebalance loop",
            "description": "this is clearly a group-coordinator issue but has no team field",
        })
        # Must still return 400 — no guessing
        assert resp.status_code == 400

    def test_consumer_team_ticket_routed_correctly(self):
        resp = client.post("/tickets", json={
            "team": "group-coordinator",
            "title": "rebalance storm on checkout topic",
        })
        assert resp.status_code == 202
        assert resp.json()["routed_to"] == "group-coordinator"

    def test_oss_kafka_ticket_routed_correctly(self):
        resp = client.post("/tickets", json={
            "team": "kafka-clients",
            "title": "KIP-518 edge case",
        })
        assert resp.status_code == 202
        assert resp.json()["routed_to"] == "kafka-clients"
