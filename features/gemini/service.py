import os
import json
import base64
import aiohttp
from typing import Any
from pathlib import Path
from features.gemini.schemas import GeminiKey, GeminiError
from core.config import DEFAULT_GEMINI_MODEL, DEFAULT_GEMINI_FALLBACK_MODELS

class GeminiKeyManager:
    def __init__(self, keys: list[GeminiKey]):
        self.keys = keys
        self.active_index = 0
        self.last_errors: dict[str, str] = {}
        self.last_success: str | None = None

    @property
    def active_key(self) -> GeminiKey | None:
        if not self.keys:
            return None
        return self.keys[self.active_index]

    def status(self) -> dict[str, Any]:
        return {
            "model": DEFAULT_GEMINI_MODEL,
            "fallback_models": get_gemini_models(),
            "search_grounding_default": True,
            "active": self.active_key.name if self.active_key else None,
            "last_success": self.last_success,
            "keys": [
                {
                    "number": index + 1,
                    "name": key.name,
                    "active": index == self.active_index,
                    "looks_valid": looks_like_gemini_key(key.value),
                    "last_error": self.last_errors.get(key.name),
                }
                for index, key in enumerate(self.keys)
            ],
        }

    def switch(self, key_ref: str | int) -> GeminiKey:
        if not self.keys:
            raise GeminiError("No Gemini keys are configured in .env")

        ref = str(key_ref).strip()
        if ref.isdigit():
            index = int(ref) - 1
            if 0 <= index < len(self.keys):
                self.active_index = index
                return self.keys[index]

        for index, key in enumerate(self.keys):
            if key.name.lower() == ref.lower():
                self.active_index = index
                return key

        raise GeminiError(f"Unknown Gemini key: {key_ref}")

    def ordered_keys(self) -> list[tuple[int, GeminiKey]]:
        if not self.keys:
            return []
        return [
            ((self.active_index + offset) % len(self.keys), self.keys[(self.active_index + offset) % len(self.keys)])
            for offset in range(len(self.keys))
        ]

def discover_gemini_keys() -> list[GeminiKey]:
    keys: list[GeminiKey] = []
    seen: set[str] = set()
    env_path = Path(".env")

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue

            name, _ = line.split("=", 1)
            name = name.strip()
            if not name.lower().endswith("_gemini") or name.lower() in seen:
                continue

            value = os.getenv(name)
            if value:
                keys.append(GeminiKey(name=name, value=value.strip().strip("\"'")))
                seen.add(name.lower())

    for name, value in os.environ.items():
        if name.lower().endswith("_gemini") and name.lower() not in seen and value:
            keys.append(GeminiKey(name=name, value=value.strip().strip("\"'")))
            seen.add(name.lower())

    return keys

def looks_like_gemini_key(value: str) -> bool:
    return value.startswith(("AIza", "AQ."))

gemini_manager = GeminiKeyManager(discover_gemini_keys())

def resolve_gemini_model(model: str | None = None) -> str:
    if not model or model.strip().lower() in {"string", "default"}:
        return DEFAULT_GEMINI_MODEL
    return model.strip()

def get_gemini_models(model: str | None = None) -> list[str]:
    configured = os.getenv("GEMINI_FALLBACK_MODELS")
    primary_model = model.strip() if (model and model.strip().lower() not in {"string", "default"}) else DEFAULT_GEMINI_MODEL
    
    models = [primary_model]
    if configured:
        models.extend(item.strip() for item in configured.split(",") if item.strip())
    else:
        models.extend(DEFAULT_GEMINI_FALLBACK_MODELS)

    deduped: list[str] = []
    for item in models:
        if item not in deduped:
            deduped.append(item)
    return deduped

def extract_interaction_text(data: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    if text := data.get("output_text"):
        return str(text).strip(), extract_interaction_citations(data)

    for step in data.get("steps", []):
        if step.get("type") != "model_output":
            continue
        for block in step.get("content", []):
            if block.get("type") == "text" and block.get("text"):
                return str(block["text"]).strip(), extract_interaction_citations(data)

    raise GeminiError(f"Unexpected Gemini interaction response: {data}")

def extract_interaction_citations(data: dict[str, Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for step in data.get("steps", []):
        if step.get("type") != "model_output":
            continue
        for block in step.get("content", []):
            for annotation in block.get("annotations", []) or []:
                if annotation.get("type") == "url_citation" and annotation.get("url"):
                    citations.append(
                        {
                            "title": annotation.get("title") or annotation["url"],
                            "url": annotation["url"],
                        }
                    )
    return citations

async def call_gemini_generate_content(api_key: str, prompt: str, model: str) -> dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }
    headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=payload) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                message = data.get("error", {}).get("message", str(data))
                raise GeminiError(f"HTTP {response.status}: {message}")

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiError(f"Unexpected Gemini response: {data}") from exc

    if not text:
        raise GeminiError("Gemini returned an empty response")
    return {"text": text, "citations": []}

async def call_gemini_interactions(api_key: str, prompt: str, model: str, use_search: bool) -> dict[str, Any]:
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    payload: dict[str, Any] = {"model": model, "input": prompt}
    if use_search:
        payload["tools"] = [{"type": "google_search"}]

    headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=payload) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                message = data.get("error", {}).get("message", str(data))
                raise GeminiError(f"HTTP {response.status}: {message}")

    text, citations = extract_interaction_text(data)
    if not text:
        raise GeminiError("Gemini returned an empty response")
    return {"text": text, "citations": citations}

async def call_gemini_api(api_key: str, prompt: str, model: str, use_search: bool) -> dict[str, Any]:
    if use_search:
        return await call_gemini_interactions(api_key, prompt, model, use_search=True)
    return await call_gemini_generate_content(api_key, prompt, model)

async def generate_with_gemini_fallback(
    prompt: str,
    model: str | None = None,
    use_search: bool = True,
) -> dict[str, Any]:
    if not gemini_manager.keys:
        raise GeminiError("No Gemini keys are configured in .env")

    models = get_gemini_models(model)
    errors: list[str] = []

    for selected_model in models:
        for index, key in gemini_manager.ordered_keys():
            try:
                result = await call_gemini_api(key.value, prompt, selected_model, use_search)
                gemini_manager.active_index = index
                gemini_manager.last_errors.pop(key.name, None)
                gemini_manager.last_success = key.name
                return {
                    "text": result["text"],
                    "citations": result["citations"],
                    "key": key.name,
                    "model": selected_model,
                    "search_grounding": use_search,
                }
            except Exception as exc:
                error = str(exc)
                gemini_manager.last_errors[key.name] = error
                errors.append(f"{selected_model}/{key.name}: {error}")

    raise GeminiError("All Gemini models and keys failed. " + " | ".join(errors))


async def generate_with_gemini_vision(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    model: str | None = None,
) -> dict[str, Any]:
    """Send an image + text prompt to Gemini using base64 inline_data.
    Supports JPEG, PNG, WEBP, GIF, PDF (< 20 MB).
    Returns the same dict shape as generate_with_gemini_fallback.
    """
    if not gemini_manager.keys:
        raise GeminiError("No Gemini keys are configured in .env")

    resolved_model = resolve_gemini_model(model)
    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH",        "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",  "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT",  "threshold": "BLOCK_NONE"},
    ]

    payload: dict[str, Any] = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": b64_data}},
                {"text": prompt},
            ]
        }],
        "safetySettings": safety_settings,
    }

    errors: list[str] = []
    for index, key in gemini_manager.ordered_keys():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{resolved_model}:generateContent"
        headers = {"Content-Type": "application/json", "X-goog-api-key": key.value}
        timeout = aiohttp.ClientTimeout(total=90)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    data = await response.json(content_type=None)
                    if response.status >= 400:
                        msg = data.get("error", {}).get("message", str(data))
                        raise GeminiError(f"HTTP {response.status}: {msg}")

            parts = data["candidates"][0]["content"]["parts"]
            text  = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                raise GeminiError("Gemini vision returned empty response")

            gemini_manager.active_index = index
            gemini_manager.last_errors.pop(key.name, None)
            gemini_manager.last_success = key.name
            return {"text": text, "citations": [], "key": key.name, "model": resolved_model}

        except Exception as exc:
            error = str(exc)
            gemini_manager.last_errors[key.name] = error
            errors.append(f"{resolved_model}/{key.name}: {error}")

    raise GeminiError("All Gemini vision attempts failed. " + " | ".join(errors))


async def stream_gemini_api(api_key: str, prompt: str, model: str, use_search: bool):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
    payload: dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
        ]
    }
    if use_search:
        payload["tools"] = [{"googleSearch": {}}]

    headers = {"Content-Type": "application/json", "X-goog-api-key": api_key}
    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status >= 400:
                data = await response.json(content_type=None)
                message = data.get("error", {}).get("message", str(data)) if isinstance(data, dict) else str(data)
                raise GeminiError(f"HTTP {response.status}: {message}")
            
            async for line in response.content:
                if line:
                    decoded = line.decode('utf-8').strip()
                    if decoded.startswith("data: "):
                        json_str = decoded[6:]
                        if json_str == "[DONE]":
                            break
                        try:
                            data = json.loads(json_str)
                            if "candidates" in data and len(data["candidates"]) > 0:
                                candidate = data["candidates"][0]
                                if "content" in candidate and "parts" in candidate["content"]:
                                    text = "".join(part.get("text", "") for part in candidate["content"]["parts"])
                                    if text:
                                        yield text
                                elif "finishReason" in candidate and candidate["finishReason"] != "STOP":
                                    yield f"\n[Stream terminated: {candidate['finishReason']}]"
                        except Exception as e:
                            pass

async def stream_with_gemini_fallback(prompt: str, model: str | None = None, use_search: bool = True):
    if not gemini_manager.keys:
        raise GeminiError("No Gemini keys are configured in .env")

    models = get_gemini_models(model)
    errors: list[str] = []

    for selected_model in models:
        for index, key in gemini_manager.ordered_keys():
            try:
                # If the first chunk yields successfully, we consider it a success and yield the rest
                async for chunk in stream_gemini_api(key.value, prompt, selected_model, use_search):
                    gemini_manager.active_index = index
                    gemini_manager.last_errors.pop(key.name, None)
                    gemini_manager.last_success = key.name
                    yield chunk
                return
            except Exception as exc:
                error = str(exc)
                gemini_manager.last_errors[key.name] = error
                errors.append(f"{selected_model}/{key.name}: {error}")

    raise GeminiError("All Gemini models and keys failed. " + " | ".join(errors))

def format_gemini_status() -> str:
    if not gemini_manager.keys:
        return "No Gemini keys found in .env."

    lines = [
        f"Gemini model: {DEFAULT_GEMINI_MODEL}",
        f"Fallback models: {', '.join(get_gemini_models())}",
        "Google Search grounding: on",
        "Keys:",
    ]
    for key in gemini_manager.status()["keys"]:
        marker = "*" if key["active"] else "-"
        warning = "" if key["looks_valid"] else " | check key format"
        error = f" | last error: {key['last_error']}" if key["last_error"] else ""
        lines.append(f"{marker} {key['number']}. {key['name']}{warning}{error}")
    return "\n".join(lines)

def format_gemini_answer(result: dict[str, Any]) -> str:
    text = result["text"]
    citations = result.get("citations") or []
    if not citations:
        return text

    unique_sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for citation in citations:
        url = citation["url"]
        if url in seen:
            continue
        seen.add(url)
        unique_sources.append(citation)

    source_lines = [
        f"{index}. {source['title']}: {source['url']}"
        for index, source in enumerate(unique_sources[:5], start=1)
    ]
    return f"{text}\n\nSources:\n" + "\n".join(source_lines)
