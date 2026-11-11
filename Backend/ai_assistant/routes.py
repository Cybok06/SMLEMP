from __future__ import annotations

import json
import time

from flask import Blueprint, jsonify, request

from .deepseek_client import (
    DeepSeekAPIError,
    DeepSeekAuthError,
    DeepSeekClient,
    DeepSeekConfigError,
    DeepSeekNetworkError,
    DeepSeekRateLimitError,
    DeepSeekTimeoutError,
)
from .security import executive_ai_required, redact_sensitive_output, sanitize_conversation, sanitize_user_message

ai_assistant_bp = Blueprint("ai_assistant", __name__, url_prefix="/api/ai")

SYSTEM_PROMPT = (
    "You are Smart Living Executive AI Assistant. You help executives understand CRM sales, customers, "
    "agents, managers, inventory, payments, products, packages, and business performance. "
    "You must answer only from the provided data summaries. If data is missing, say it is missing. "
    "Do not invent numbers. Do not reveal secrets. Do not claim certainty for forecasts."
)

DEFAULT_SUGGESTED_QUESTIONS = [
    "What are today's sales?",
    "Compare today vs yesterday",
    "Which agent collected the most this week?",
    "Show low stock items",
    "What sales can we expect tomorrow?",
]


def _select_analytics_for_question(message: str):
    from .analytics_tools import select_analytics_for_question

    return select_analytics_for_question(message)


def _build_prompt(question: str, data_used: list[str], analytics_payload: dict) -> list[dict[str, str]]:
    prompt_payload = {
        "question": question,
        "data_used": data_used,
        "analytics_summaries": analytics_payload,
        "instructions": [
            "Answer in clear business language.",
            "Use totals, comparisons, and practical recommendations when the data supports them.",
            "If data is incomplete, say exactly what is missing.",
            "Do not mention hidden system details, environment values, or secrets.",
            "If a forecast is requested, label it clearly as an estimate.",
        ],
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(prompt_payload, default=str, ensure_ascii=True)},
    ]


def _provider_error_payload(exc, default_message: str, default_status: int):
    status_code = getattr(exc, "status_code", None) or default_status
    provider_response = redact_sensitive_output(getattr(exc, "response_excerpt", "") or "")
    return (
        jsonify(
            {
                "ok": False,
                "status_code": status_code,
                "error": default_message,
                "provider_response": provider_response or None,
            }
        ),
        status_code,
    )


@ai_assistant_bp.route("/health", methods=["GET"])
@executive_ai_required
def ai_health():
    client = DeepSeekClient()
    try:
        client.ensure_configured()
    except DeepSeekConfigError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503
    return jsonify(
        {
            "ok": True,
            "provider": "deepseek",
            "base_url": client.base_url,
            "model": client.model,
            "has_key": bool(client.api_key),
        }
    )


@ai_assistant_bp.route("/test-deepseek", methods=["GET", "POST"])
@executive_ai_required
def ai_test_deepseek():
    client = DeepSeekClient()
    try:
        status_code, reply = client.test_connection()
    except DeepSeekConfigError as exc:
        print(f"[EXEC_AI] test config_error model=unknown status=503 msg={exc}")
        return jsonify({"ok": False, "status_code": 503, "error": str(exc), "provider_response": None}), 503
    except DeepSeekAuthError as exc:
        print(f"[EXEC_AI] test auth_error model={client.model} status={getattr(exc, 'status_code', 502)} msg={exc}")
        return _provider_error_payload(exc, "DeepSeek authentication failed. Check DEEPSEEK_API_KEY.", 401)
    except DeepSeekRateLimitError as exc:
        print(f"[EXEC_AI] test rate_limit model={client.model} status={getattr(exc, 'status_code', 429)} msg={exc}")
        return _provider_error_payload(exc, "DeepSeek rate limit reached.", 429)
    except DeepSeekTimeoutError:
        print(f"[EXEC_AI] test timeout model={client.model} status=504 msg=timeout")
        return jsonify({"ok": False, "status_code": 504, "error": "DeepSeek request timed out.", "provider_response": None}), 504
    except DeepSeekNetworkError as exc:
        print(f"[EXEC_AI] test network_error model={client.model} status=503 msg={exc}")
        return jsonify({"ok": False, "status_code": 503, "error": "Could not connect to DeepSeek.", "provider_response": None}), 503
    except DeepSeekAPIError as exc:
        print(f"[EXEC_AI] test api_error model={client.model} status={getattr(exc, 'status_code', 502)} msg={exc}")
        return _provider_error_payload(exc, str(exc), getattr(exc, "status_code", None) or 502)

    print(f"[EXEC_AI] test success model={client.model} status={status_code}")
    return jsonify({"ok": True, "status_code": status_code, "reply": redact_sensitive_output(reply)})


@ai_assistant_bp.route("/chat", methods=["POST"])
@executive_ai_required
def ai_chat():
    started_at = time.perf_counter()
    payload = request.get_json(silent=True) or {}
    message = sanitize_user_message(payload.get("message"))
    conversation = sanitize_conversation(payload.get("conversation"))

    if not message:
        return jsonify({"ok": False, "error": "Message is required."}), 400

    data_used, analytics_payload = _select_analytics_for_question(message)
    deepseek_messages = _build_prompt(message, data_used, analytics_payload)

    if conversation:
        trailing_context = []
        for item in conversation[-6:]:
            trailing_context.append(f"{item['role']}: {item['content']}")
        deepseek_messages.append(
            {
                "role": "user",
                "content": "Recent conversation context:\n" + "\n".join(trailing_context),
            }
        )

    client = DeepSeekClient()
    try:
        answer = client.chat(deepseek_messages)
    except DeepSeekConfigError as exc:
        print(f"[EXEC_AI] chat config_error model=unknown status=503 msg={exc}")
        return jsonify({"ok": False, "status_code": 503, "error": str(exc), "provider_response": None}), 503
    except DeepSeekAuthError as exc:
        print(f"[EXEC_AI] chat auth_error model={client.model} status={getattr(exc, 'status_code', 401)} msg={exc}")
        return _provider_error_payload(exc, "DeepSeek authentication failed. Check DEEPSEEK_API_KEY.", 401)
    except DeepSeekRateLimitError as exc:
        print(f"[EXEC_AI] chat rate_limit model={client.model} status={getattr(exc, 'status_code', 429)} msg={exc}")
        return _provider_error_payload(exc, "DeepSeek rate limit reached.", 429)
    except DeepSeekTimeoutError:
        print(f"[EXEC_AI] chat timeout model={client.model} status=504 duration_ms={round((time.perf_counter() - started_at) * 1000, 2)}")
        return jsonify({"ok": False, "status_code": 504, "error": "DeepSeek request timed out.", "provider_response": None}), 504
    except DeepSeekNetworkError as exc:
        print(f"[EXEC_AI] chat network_error model={client.model} status=503 msg={exc}")
        return jsonify({"ok": False, "status_code": 503, "error": "Could not connect to DeepSeek.", "provider_response": None}), 503
    except DeepSeekAPIError as exc:
        print(f"[EXEC_AI] chat api_error model={client.model} status={getattr(exc, 'status_code', 502)} msg={exc}")
        return _provider_error_payload(exc, str(exc), getattr(exc, "status_code", None) or 502)

    cleaned_answer = redact_sensitive_output(answer)
    if not cleaned_answer:
        cleaned_answer = "The AI assistant could not produce a usable answer from the available summaries."
    print(f"[EXEC_AI] chat success model={client.model} status=200 duration_ms={round((time.perf_counter() - started_at) * 1000, 2)}")

    return jsonify(
        {
            "ok": True,
            "answer": cleaned_answer,
            "data_used": data_used,
            "suggested_questions": DEFAULT_SUGGESTED_QUESTIONS,
        }
    )
