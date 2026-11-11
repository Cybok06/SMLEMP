from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


class DeepSeekError(Exception):
    pass


class DeepSeekConfigError(DeepSeekError):
    pass


class DeepSeekAuthError(DeepSeekError):
    def __init__(self, message: str, *, status_code: int | None = None, response_excerpt: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_excerpt = response_excerpt


class DeepSeekRateLimitError(DeepSeekError):
    def __init__(self, message: str, *, status_code: int | None = None, response_excerpt: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_excerpt = response_excerpt


class DeepSeekAPIError(DeepSeekError):
    def __init__(self, message: str, *, status_code: int | None = None, response_excerpt: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_excerpt = response_excerpt


class DeepSeekTimeoutError(DeepSeekError):
    pass


class DeepSeekNetworkError(DeepSeekError):
    pass


class DeepSeekClient:
    def __init__(self) -> None:
        self.api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        self.base_url = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
        self.model = (os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-flash").strip()
        timeout_raw = (os.getenv("DEEPSEEK_TIMEOUT_SECONDS") or "20").strip()
        try:
            self.timeout = max(5, min(int(timeout_raw), 120))
        except Exception:
            self.timeout = 20

    def ensure_configured(self) -> None:
        if not self.api_key:
            raise DeepSeekConfigError("DEEPSEEK_API_KEY is not configured.")
        if not self.base_url:
            raise DeepSeekConfigError("DEEPSEEK_BASE_URL is not configured.")
        if not self.model:
            raise DeepSeekConfigError("DEEPSEEK_MODEL is not configured.")
        allowed_models = {"deepseek-chat", "deepseek-v4-flash"}
        if self.model not in allowed_models:
            raise DeepSeekConfigError(
                "DEEPSEEK_MODEL must be either 'deepseek-chat' or 'deepseek-v4-flash'."
            )

    @staticmethod
    def _response_excerpt(response: requests.Response) -> str:
        try:
            text = (response.text or "").strip()
        except Exception:
            return ""
        if not text:
            return ""
        return text[:240]

    def _request(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 800) -> dict[str, Any]:
        self.ensure_configured()

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        endpoint = f"{self.base_url}/chat/completions"

        try:
            response = requests.post(endpoint, json=payload, headers=headers, timeout=max(self.timeout, 45))
        except requests.Timeout as exc:
            raise DeepSeekTimeoutError("DeepSeek request timed out.") from exc
        except requests.RequestException as exc:
            raise DeepSeekNetworkError("Could not connect to DeepSeek.") from exc

        if response.status_code in {401, 403}:
            raise DeepSeekAuthError(
                "DeepSeek authentication failed. Check DEEPSEEK_API_KEY.",
                status_code=response.status_code,
                response_excerpt=self._response_excerpt(response),
            )
        if response.status_code == 402:
            raise DeepSeekAPIError(
                "DeepSeek balance is insufficient.",
                status_code=response.status_code,
                response_excerpt=self._response_excerpt(response),
            )
        if response.status_code == 422:
            raise DeepSeekAPIError(
                "DeepSeek request format/model is invalid.",
                status_code=response.status_code,
                response_excerpt=self._response_excerpt(response),
            )
        if response.status_code == 429:
            raise DeepSeekRateLimitError(
                "DeepSeek rate limit reached.",
                status_code=response.status_code,
                response_excerpt=self._response_excerpt(response),
            )
        if response.status_code >= 500:
            raise DeepSeekAPIError(
                "DeepSeek service is currently unavailable.",
                status_code=response.status_code,
                response_excerpt=self._response_excerpt(response),
            )
        if response.status_code >= 400:
            raise DeepSeekAPIError(
                "DeepSeek rejected the request.",
                status_code=response.status_code,
                response_excerpt=self._response_excerpt(response),
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise DeepSeekAPIError(
                "DeepSeek returned an invalid response.",
                status_code=response.status_code,
                response_excerpt=self._response_excerpt(response),
            ) from exc

        return payload

    def test_connection(self) -> tuple[int, str]:
        payload = self._request(
            [
                {"role": "system", "content": "You are Smart Living Executive AI Assistant."},
                {"role": "user", "content": "Say OK"},
            ],
            temperature=0.3,
            max_tokens=20,
        )
        try:
            content = payload["choices"][0]["message"]["content"]
            return 200, str(content or "").strip()
        except Exception as exc:
            raise DeepSeekAPIError("DeepSeek response did not include a chat answer.") from exc

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 800) -> str:
        payload = self._request(messages, temperature=temperature, max_tokens=max_tokens)

        try:
            content = payload["choices"][0]["message"]["content"]
        except Exception as exc:
            raise DeepSeekAPIError("DeepSeek response did not include a chat answer.") from exc

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
            return "\n".join(part for part in parts if part).strip()
        return str(content or "").strip()
