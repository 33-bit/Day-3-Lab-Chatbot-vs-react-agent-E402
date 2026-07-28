"""HTTP API for the Order Agent web application."""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .app import run_react_agent
from .prompts import CHATBOT_BASELINE_PROMPT
from .providers import get_llm_provider
from .tools import TOOL_SPECS


PROVIDER_KEYS = {
    "mock": None,
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

PROVIDER_LABELS = {
    "mock": "Offline Mock",
    "gemini": "Google Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "mistral": "Mistral AI",
    "openrouter": "OpenRouter",
}

DEFAULT_MODELS = {
    "mock": "offline",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-haiku-20240307",
    "mistral": "mistral-small-latest",
    "openrouter": "google/gemini-2.5-flash",
}

PLACEHOLDER_VALUES = {
    "your_gemini_api_key_here",
    "your_openai_api_key_here",
    "your_anthropic_api_key_here",
    "your_mistral_api_key_here",
    "your_openrouter_api_key_here",
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    mode: Literal["baseline", "react"] = "baseline"
    provider: str | None = None
    confirm_actions: bool = False


class ProviderInfo(BaseModel):
    id: str
    label: str
    model: str
    configured: bool


class TraceEvent(BaseModel):
    kind: Literal["system", "model", "tool"]
    label: str
    detail: str
    status: Literal["pending", "completed", "error"]


class ChatResponse(BaseModel):
    id: str
    mode: Literal["baseline", "react"]
    status: Literal["completed", "error"]
    content: str
    provider: ProviderInfo
    trace: list[TraceEvent]


class AppConfig(BaseModel):
    product_name: str
    active_provider: ProviderInfo
    providers: list[ProviderInfo]
    tools: list[dict]
    examples: list[dict]


def _is_configured(provider_name: str) -> bool:
    key_name = PROVIDER_KEYS.get(provider_name)
    if key_name is None:
        return provider_name == "mock"

    value = os.getenv(key_name, "").strip()
    return bool(value and value not in PLACEHOLDER_VALUES)


def _provider_info(provider_name: str | None = None) -> ProviderInfo:
    normalized = (provider_name or os.getenv("LLM_PROVIDER") or "mock").lower().strip()
    if normalized not in PROVIDER_KEYS:
        normalized = "mock"

    active_provider = (os.getenv("LLM_PROVIDER") or "mock").lower().strip()
    model_override = None if normalized == active_provider else DEFAULT_MODELS[normalized]
    provider = get_llm_provider(normalized, model=model_override)
    return ProviderInfo(
        id=normalized,
        label=PROVIDER_LABELS[normalized],
        model=getattr(provider, "model_name", "offline"),
        configured=_is_configured(normalized),
    )


def _tool_payload() -> list[dict]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "side_effect": spec["side_effect"],
            "parameters": list(spec["parameters"].keys()),
        }
        for name, spec in TOOL_SPECS.items()
    ]


def _example_payload() -> list[dict]:
    with (PROJECT_ROOT / "config" / "test_cases.json").open(
        "r",
        encoding="utf-8",
    ) as config_file:
        test_cases = json.load(config_file)
    return [
        {
            "id": test_case["id"],
            "category": test_case["category"],
            "question": test_case["question"],
        }
        for test_case in test_cases[:4]
    ]


def _run_baseline(user_query: str, provider) -> str:
    """Call the existing provider with the project's baseline prompt."""
    return provider.generate(user_query, system_prompt=CHATBOT_BASELINE_PROMPT)


def _observation_failed(content: str) -> bool:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return payload.get("success") is False


def _react_trace_payload(raw_trace: list[dict]) -> list[TraceEvent]:
    """Convert the secured ReAct trace into UI events without raw thoughts."""

    converted: list[TraceEvent] = []
    for event in raw_trace:
        step = event.get("step", "?")
        event_type = event.get("type", "system")

        if event_type == "model":
            converted.append(
                TraceEvent(
                    kind="model",
                    label=f"Model output, bước {step}",
                    detail=(
                        "Đã nhận lượt suy luận từ model. Nội dung Thought thô được "
                        "ẩn; Action và Observation đã kiểm chứng hiển thị riêng."
                    ),
                    status="completed",
                )
            )
            continue

        if event_type == "action":
            arguments = json.dumps(
                event.get("arguments", {}),
                ensure_ascii=False,
                sort_keys=True,
            )
            converted.append(
                TraceEvent(
                    kind="tool",
                    label=f"Action: {event.get('tool', 'unknown')}",
                    detail=arguments,
                    status="completed",
                )
            )
            continue

        if event_type == "observation":
            content = str(event.get("content", ""))
            failed = bool(event.get("error_code")) or _observation_failed(content)
            tool_name = event.get("tool") or "system"
            converted.append(
                TraceEvent(
                    kind="tool" if event.get("tool") else "system",
                    label=f"Observation: {tool_name}",
                    detail=content,
                    status="error" if failed else "completed",
                )
            )
            continue

        if event_type == "guardrail":
            converted.append(
                TraceEvent(
                    kind="system",
                    label=f"Guardrail: {event.get('error_code', 'SAFE_STOP')}",
                    detail=str(event.get("content", "Agent đã dừng an toàn.")),
                    status="error",
                )
            )
            continue

        if event_type == "final_answer":
            converted.append(
                TraceEvent(
                    kind="model",
                    label="Final answer",
                    detail=f"Hoàn tất tại bước {step}.",
                    status="completed",
                )
            )
            continue

        converted.append(
            TraceEvent(
                kind="system",
                label=f"System event, bước {step}",
                detail=str(event.get("content", "")),
                status="completed",
            )
        )

    return converted


app = FastAPI(
    title="Order Agent API",
    description="Backend for the baseline chatbot and secure ReAct agent.",
    version="0.1.0",
)

allowed_origins = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5175",
    "http://127.0.0.1:5175",
}
if configured_origin := os.getenv("FRONTEND_ORIGIN"):
    allowed_origins.add(configured_origin.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(allowed_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/")
def root() -> dict:
    return {"name": "Order Agent API", "docs": "/docs", "health": "/api/health"}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "order-agent-api"}


@app.get("/api/config", response_model=AppConfig)
def config() -> AppConfig:
    return AppConfig(
        product_name="Order Agent",
        active_provider=_provider_info(),
        providers=[_provider_info(name) for name in PROVIDER_KEYS],
        tools=_tool_payload(),
        examples=_example_payload(),
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    provider_info = _provider_info(payload.provider)

    if payload.mode == "react":
        provider = get_llm_provider(provider_info.id, model=provider_info.model)
        confirm_action = (
            lambda _tool_name, _arguments: payload.confirm_actions
        )
        result = await run_in_threadpool(
            run_react_agent,
            payload.message.strip(),
            provider,
            confirm_action=confirm_action,
        )
        status: Literal["completed", "error"] = (
            "completed" if result.stop_reason == "final_answer" else "error"
        )
        return ChatResponse(
            id=str(uuid4()),
            mode="react",
            status=status,
            content=result.answer,
            provider=provider_info,
            trace=_react_trace_payload(result.trace),
        )

    provider = get_llm_provider(provider_info.id, model=provider_info.model)
    response = await run_in_threadpool(
        _run_baseline,
        payload.message.strip(),
        provider,
    )
    is_error = response.lstrip().startswith("[") and (
        "Error" in response or "Exception" in response
    )
    trace_status: Literal["completed", "error"] = "error" if is_error else "completed"

    return ChatResponse(
        id=str(uuid4()),
        mode="baseline",
        status=trace_status,
        content=response,
        provider=provider_info,
        trace=[
            TraceEvent(
                kind="model",
                label="Baseline LLM response",
                detail="Không gọi tool. Phản hồi được tạo trực tiếp từ model provider.",
                status=trace_status,
            )
        ],
    )
