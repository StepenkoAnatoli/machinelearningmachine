from fastapi.testclient import TestClient

from machinelearningmachine.server.app import app


def test_server_index():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "Inter-Module Agent Mesh" in response.text


def test_server_agents_endpoint():
    client = TestClient(app)
    response = client.get("/api/agents")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    agent_ids = [a["agent_id"] for a in data]
    assert "arena-ai" in agent_ids
    assert "copilot" in agent_ids
    assert "claude" in agent_ids
    assert "gpt" in agent_ids


def test_server_run_p2p():
    client = TestClient(app)
    payload = {
        "topology": "p2p",
        "prompt": "Create a thread-safe singleton pattern in Python",
        "from_agent": "arena-ai",
        "to_agent": "copilot",
        "turns": 2,
    }
    response = client.post("/api/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert len(data["messages"]) == 2


def test_server_history_and_clear():
    client = TestClient(app)
    # Check history
    response = client.get("/api/history")
    assert response.status_code == 200
    # Clear history
    clear_resp = client.post("/api/clear")
    assert clear_resp.status_code == 200
    assert clear_resp.json()["status"] == "cleared"
    # History should now be empty
    response_after = client.get("/api/history")
    assert response_after.json() == []


def test_server_add_custom_agent():
    client = TestClient(app)
    new_agent = {
        "agent_id": "test-devops-agent",
        "name": "DevOps Bot",
        "role": "CI/CD & Kubernetes Specialist",
        "system_prompt": "Write Dockerfiles and Helm charts.",
        "color": "#3b82f6",
        "avatar": "🐳",
    }
    response = client.post("/api/agents", json=new_agent)
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Verify agent appears in list
    agents_resp = client.get("/api/agents")
    agent_ids = [a["agent_id"] for a in agents_resp.json()]
    assert "test-devops-agent" in agent_ids
