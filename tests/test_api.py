import src.api as api_module
from fastapi.testclient import TestClient

from src.app import ReactResult
from src.api import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "order-agent-api"}


def test_config_exposes_real_tools_and_providers():
    response = client.get("/api/config")
    assert response.status_code == 200
    payload = response.json()
    assert payload["product_name"] == "Order Agent"
    assert {tool["name"] for tool in payload["tools"]} == {
        "search_order",
        "create_return_request",
        "create_exchange_request",
    }
    assert any(provider["id"] == "mock" for provider in payload["providers"])


def test_provider_models_do_not_leak_across_provider_selection(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    monkeypatch.setenv("LLM_MODEL", "mistral-medium-latest")

    response = client.get("/api/config")
    providers = {provider["id"]: provider for provider in response.json()["providers"]}

    assert providers["mistral"]["model"] == "mistral-medium-latest"
    assert providers["openai"]["model"] == "gpt-4o-mini"


def test_baseline_chat_uses_mock_provider_without_tools():
    response = client.post(
        "/api/chat",
        json={"message": "Xin chào", "mode": "baseline", "provider": "mock"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "baseline"
    assert payload["provider"]["id"] == "mock"
    assert all(event["kind"] != "tool" for event in payload["trace"])


def test_react_chat_executes_real_mock_tool_without_exposing_raw_thoughts():
    response = client.post(
        "/api/chat",
        json={
            "message": "Tra cứu đơn DH001 với số điện thoại 0901234567",
            "mode": "react",
            "provider": "mock",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["mode"] == "react"
    assert "Đã tra cứu DH001 thành công" in payload["content"]

    labels = {event["label"] for event in payload["trace"]}
    assert "Action: search_order" in labels
    assert "Observation: search_order" in labels

    trace_text = "\n".join(
        f"{event['label']}\n{event['detail']}" for event in payload["trace"]
    )
    assert "Thought:" not in trace_text
    assert "Tôi cần xác thực và tra cứu đơn hàng bằng tool." not in trace_text


def test_react_chat_passes_one_shot_mutation_consent(monkeypatch):
    consent_decisions: list[bool] = []

    def fake_run_react_agent(user_query, provider, *, confirm_action):
        del user_query, provider
        consent_decisions.append(
            confirm_action("create_return_request", {"order_id": "DH001"})
        )
        return ReactResult(
            answer="Đã kiểm tra cờ xác nhận.",
            trace=[{"step": 1, "type": "final_answer"}],
            iterations=1,
            stop_reason="final_answer",
        )

    monkeypatch.setattr(api_module, "run_react_agent", fake_run_react_agent)

    for confirm_actions in (False, True):
        response = client.post(
            "/api/chat",
            json={
                "message": "Tạo yêu cầu trả hàng cho DH001",
                "mode": "react",
                "provider": "mock",
                "confirm_actions": confirm_actions,
            },
        )
        assert response.status_code == 200

    assert consent_decisions == [False, True]
