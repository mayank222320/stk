import os
import json
import re
import aiohttp
from typing import Any
from pathlib import Path
from pydantic import BaseModel

class GrokKey(BaseModel):
    name: str
    value: str

class GrokError(Exception):
    pass

class GrokKeyManager:
    def __init__(self, keys: list[GrokKey]):
        self.keys = keys
        self.active_index = 0

    @property
    def active_key(self) -> GrokKey | None:
        if not self.keys:
            return None
        return self.keys[self.active_index]

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active_key.name if self.active_key else None,
            "keys": [
                {
                    "number": index + 1,
                    "name": key.name,
                    "active": index == self.active_index,
                    "looks_valid": looks_like_grok_key(key.value),
                }
                for index, key in enumerate(self.keys)
            ],
        }

    def switch(self, key_ref: str | int) -> GrokKey:
        if not self.keys:
            raise GrokError("No Grok keys are configured in .env")

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

        raise GrokError(f"Unknown Grok key: {key_ref}")

def discover_grok_keys() -> list[GrokKey]:
    keys: list[GrokKey] = []
    seen: set[str] = set()
    env_path = Path(".env")
    
    target_keys = {
        "ind_grok", "mk_grok", "kalpmah_grok", "cinfo_grok", 
        "hitman_grok", "kNumetry_grok", "k2003_grok", "kmahajn_grok", "kmain_grok"
    }

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue

            name, _ = line.split("=", 1)
            name = name.strip()
            if not name.lower().endswith("_grok") or name.lower() in seen:
                continue

            value = os.getenv(name)
            if not value:
                # If not in os.getenv, get from file line
                value = line.split("=", 1)[1].strip().strip("\"'")
                
            if value:
                keys.append(GrokKey(name=name, value=value.strip().strip("\"'")))
                seen.add(name.lower())

    for name, value in os.environ.items():
        if name.lower().endswith("_grok") and name.lower() not in seen and value:
            keys.append(GrokKey(name=name, value=value.strip().strip("\"'")))
            seen.add(name.lower())

    return keys

def looks_like_grok_key(value: str) -> bool:
    return value.startswith("xai-") or value.startswith("gsk_")

grok_manager = GrokKeyManager(discover_grok_keys())

async def get_sentiment(symbol: str) -> dict:
    if not grok_manager.active_key:
        return {"error": "No Grok keys available"}
        
    active_key_val = grok_manager.active_key.value
    is_groq = active_key_val.startswith("gsk_")

    url = "https://api.groq.com/openai/v1/chat/completions" if is_groq else "https://api.x.ai/v1/chat/completions"
    model_name = "llama-3.3-70b-versatile" if is_groq else "grok-2-latest"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {active_key_val}"
    }
    
    prompt = (
        f"Analyze {symbol} stock on x_twitter_analysis. Return ONLY JSON with these exact fields: "
        '{"sentiment": "bullish|bearish|neutral", "confidence": 0-100, "summary": "...", "key_points": ["..."], "bear_case": "...", "source": "x_twitter_analysis"}'
    )
    
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=30) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    return {"error": f"HTTP {resp.status}: {data}"}
                
                content = data["choices"][0]["message"]["content"]
                
                # try to parse json from markdown block if any
                import json, re
                text = content.strip()
                text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
                text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1:
                    text = text[start:end+1]
                    
                return json.loads(text)
    except Exception as exc:
        return {"error": str(exc)}

async def analyze_news(headline: str, summary: str, url: str) -> dict:
    """Analyze breaking news for market impact. Auto-rotates keys on 429."""
    if not grok_manager.keys:
        return {"error": "No Grok keys configured"}

    # Try each available key once, stop as soon as one succeeds
    num_keys = len(grok_manager.keys)
    for attempt in range(num_keys):
        active_key_val = grok_manager.active_key.value  # type: ignore[union-attr]
        is_groq = active_key_val.startswith("gsk_")
        api_url = "https://api.groq.com/openai/v1/chat/completions" if is_groq else "https://api.x.ai/v1/chat/completions"
        model_name = "llama-3.3-70b-versatile" if is_groq else "grok-2-latest"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {active_key_val}"}

        prompt = (
            "Analyze this breaking news for Indian stock market impact.\n"
            f"Headline: {headline}\n"
            f"Summary: {summary}\n"
            f"URL: {url}\n\n"
            "Return ONLY valid JSON with these exact fields:\n"
            '{"sentiment": "bullish|bearish|neutral", "confidence": 0-100, '
            '"impacted_symbols": ["NSE_SYMBOL"], "summary": "...", "trade_setup": "..."}'
        )

        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    data = await resp.json()
                    if resp.status == 429:
                        # Rate limited — rotate to next key and retry
                        next_idx = (grok_manager.active_index + 1) % num_keys
                        grok_manager.switch(str(next_idx + 1))
                        print(f"[Grok] Rate limited, rotating to key {next_idx + 1}")
                        continue
                    if resp.status >= 400:
                        return {"error": f"HTTP {resp.status}: {data}"}

                    content = data["choices"][0]["message"]["content"]
                    text = content.strip()
                    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
                    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
                    start = text.find("{")
                    end = text.rfind("}")
                    if start != -1 and end != -1:
                        text = text[start:end + 1]
                    return json.loads(text)
        except aiohttp.ClientError as exc:
            return {"error": f"Network error: {exc}"}
        except json.JSONDecodeError as exc:
            return {"error": f"Invalid JSON from model: {exc}"}
        except Exception as exc:
            return {"error": str(exc)}

    return {"error": "All Grok keys are rate-limited"}
