"""Thin LLM helpers — Groq for text, Gemini for vision. Both return parsed JSON."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv

from .paths import ROOT

load_dotenv(ROOT / ".env")

GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_FALLBACK = "gemini-2.0-flash"


def _groq_keys() -> list[str]:
    keys = [os.getenv(f"GROQ_API_KEY_{i}") for i in range(1, 10)]
    keys = [k for k in keys if k]
    if not keys and os.getenv("GROQ_API_KEY"):
        keys = [os.getenv("GROQ_API_KEY")]
    return keys


def _extract_json(raw: str):
    raw = raw.strip()
    if "```" in raw:
        m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
        if m:
            raw = m.group(1).strip()
    s, e = raw.find("{"), raw.rfind("}")
    if s != -1 and e > s:
        raw = raw[s : e + 1]
    return json.loads(raw)


def groq_json(system: str, prompt: str, *, temperature: float = 0.1, max_tokens: int = 1500) -> dict:
    from groq import Groq

    keys = _groq_keys()
    if not keys:
        raise EnvironmentError("No GROQ_API_KEY_* in .env")
    last_err = None
    for key in keys:
        client = Groq(api_key=key)
        for attempt in range(3):
            try:
                r = client.chat.completions.create(
                    model=GROQ_TEXT_MODEL,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                )
                return _extract_json(r.choices[0].message.content)
            except Exception as e:  # noqa: BLE001
                last_err = e
                s = str(e).lower()
                if "rate" in s or "429" in s:
                    time.sleep(4 * (attempt + 1))
                    continue
                if any(t in s for t in ("413", "invalid_api_key", "401")):
                    break
                time.sleep(1)
    raise RuntimeError(f"groq_json failed: {last_err}")


def _gemini_client():
    from google import genai

    key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not key:
        raise EnvironmentError("GOOGLE_API_KEY not set")
    return genai.Client(api_key=key)


def gemini_json(
    system: str,
    prompt: str,
    *,
    images: list[str | Path] | None = None,
    temperature: float = 0.1,
    max_tokens: int = 4000,
) -> dict:
    """images: local file paths or http(s) URLs."""
    from google.genai import types as gt

    client = _gemini_client()
    parts: list = []
    for img in images or []:
        s = str(img)
        try:
            if s.startswith("http"):
                ext = s.split("?")[0].rsplit(".", 1)[-1].lower()
                mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                        "webp": "image/webp"}.get(ext, "image/jpeg")
                parts.append(gt.Part.from_uri(file_uri=s, mime_type=mime))
            else:
                p = Path(s)
                mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                        ".webp": "image/webp"}.get(p.suffix.lower(), "image/jpeg")
                parts.append(gt.Part.from_bytes(data=p.read_bytes(), mime_type=mime))
        except Exception:  # noqa: BLE001
            pass
    parts.append(prompt)

    last_err = None
    for model in (GEMINI_MODEL, GEMINI_FALLBACK):
        for attempt in range(3):
            try:
                r = client.models.generate_content(
                    model=model,
                    contents=parts,
                    config=gt.GenerateContentConfig(
                        system_instruction=system,
                        temperature=temperature,
                        max_output_tokens=max_tokens,
                        thinking_config=gt.ThinkingConfig(thinking_budget=0),
                        response_mime_type="application/json",
                    ),
                )
                return _extract_json(r.text)
            except Exception as e:  # noqa: BLE001
                last_err = e
                s = str(e).lower()
                if "rate" in s or "429" in s or "quota" in s:
                    time.sleep(8 * (attempt + 1))
                    continue
                if "503" in s or "unavailable" in s or "overloaded" in s:
                    break
                time.sleep(1)
    raise RuntimeError(f"gemini_json failed: {last_err}")
