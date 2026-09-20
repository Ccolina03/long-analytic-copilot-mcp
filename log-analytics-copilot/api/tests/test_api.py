"""API tests — start a demo ticket and read back the trace."""

import time

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def _wait(ticket_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/tickets/{ticket_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in {"done", "error"}:
            return data
        time.sleep(0.05)
    raise AssertionError("ticket did not finish in time")


class TestHealth:
    def test_ok(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert "mirrormaker" in resp.json()["implemented"]
        assert "kafka-security" in resp.json()["implemented"]


class TestDirectory:
    def test_includes_unimplemented_teams(self):
        teams = {t["agent_id"]: t for t in client.get("/api/directory").json()["teams"]}
        assert teams["kafka-security"]["implemented"] is True
        assert teams["kafka-storage"]["implemented"] is False


class TestDemoRun:
    def test_demo_discovers_security_and_skips_storage(self):
        started = client.post("/api/demo")
        assert started.status_code == 200
        ticket_id = started.json()["id"]
        data = _wait(ticket_id)
        assert data["status"] == "done"
        assert data["finding"]["owning_agent"] == "mirrormaker"
        kinds = [e["kind"] for e in data["events"]]
        assert "peer_decision" in kinds
        assert "request" in kinds
        assert "response" in kinds
        assert "artifact" in kinds
        decisions = [e for e in data["events"] if e["kind"] == "peer_decision"]
        consulted = {e["to_agent"] for e in decisions if e["data"].get("consult")}
        skipped = {e["to_agent"] for e in decisions if e["data"].get("consult") is False}
        assert "kafka-security" in consulted
        assert "kafka-storage" in skipped
        assert data["doc"].startswith("# ")


class TestJiraWebhook:
    def test_maps_issue_to_mirrormaker_and_runs(self):
        resp = client.post(
            "/api/webhooks/jira",
            json={
                "issue": {
                    "key": "KAFKA-18231",
                    "self": "https://issues.apache.org/jira/rest/api/2/issue/1",
                    "fields": {
                        "summary": "Group discovery is slow",
                        "description": "p99 is 8-12s on 10k groups",
                        "priority": {"name": "High"},
                        "labels": ["mirrormaker", "performance"],
                        "components": [{"name": "mirrormaker"}],
                    },
                }
            },
        )
        assert resp.status_code == 200
        ticket_id = resp.json()["id"]
        data = _wait(ticket_id)
        assert data["status"] == "done"
        assert data["ticket"]["source"] == "jira"
        assert data["ticket"]["team"] == "mirrormaker"
        assert "KAFKA-18231" in data["ticket"]["source_url"]
        assert data["ticket"]["title"] == "Group discovery is slow"
