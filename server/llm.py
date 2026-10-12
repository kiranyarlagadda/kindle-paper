"""Small client for an OpenAI-compatible gateway (a local Qwen model here), used by the paper build.

OpenAI-compatible chat completions. Qwen is a thinking model, so <think> blocks
are stripped. Every call is optional: on any failure it returns None and the
caller uses its deterministic fallback. A shared time budget caps the whole
build, and after three straight failures (GPU busy, another app holding VRAM) the
rest of the build skips the model instead of waiting on timeouts.

Env: LLM_URL, LLM_KEY, LLM_MODEL, LLM_LABEL, PAPER_LLM_BUDGET (seconds, default 900).
"""

import json
import os
import re
import time
from urllib.request import Request, urlopen

URL = os.environ.get("LLM_URL", "").rstrip("/")
KEY = os.environ.get("LLM_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "auto")
LABEL = os.environ.get("LLM_LABEL", "Qwen")  # how the paper credits the model

STYLE = ("Write plain, factual, neutral English. Report what happened and who said it; no "
         "adjectives of judgment, no speculation, no opinions. Never use em dashes or en dashes: "
         "use commas, periods or parentheses instead.")

_state = {"deadline": 0.0, "fails": 0, "calls": 0, "spent": 0.0, "disabled": False}


def start(budget=None, disabled=False):
    budget = float(budget or os.environ.get("PAPER_LLM_BUDGET", "900"))
    _state.update(deadline=time.time() + budget, fails=0, calls=0, spent=0.0, disabled=disabled)


def left():
    return _state["deadline"] - time.time()


def available():
    return bool(URL and KEY) and not _state["disabled"] and _state["fails"] < 3 and left() > 20


def stats():
    return {k: _state[k] for k in ("calls", "fails", "spent")}


def strip_think(text):
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    if "<think>" in text:  # unterminated: the model ran out of tokens mid-thought
        text = text.split("<think>")[0]
    return text.strip()


def dedash(text):
    """House style: no long dashes anywhere. Turn them into commas or periods."""
    if not text or ("\u2014" not in text and "\u2013" not in text):
        return text
    text = re.sub(r"\s*[\u2014\u2013]\s*$", ".", text, flags=re.M)  # trailing dash ends a sentence
    text = re.sub(r"^\s*[\u2014\u2013]\s*", "", text, flags=re.M)   # leading attribution dash
    text = re.sub(r"(?<=\d)\u2013(?=\d)", "-", text)                # number ranges keep a hyphen
    text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", text)
    text = re.sub(r",\s*([.,;:!?])", r"\1", text)
    return text


def chat(system, user, max_tokens=1500, timeout=240, temperature=0.2):
    """Model reply text with thinking removed, or None."""
    if not available():
        return None
    timeout = max(10, min(timeout, left()))
    body = json.dumps({
        "model": MODEL, "temperature": temperature, "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": f"{system}\n\n{STYLE}"},
                     {"role": "user", "content": user}],
    }).encode()
    req = Request(f"{URL}/v1/chat/completions", data=body, headers={
        "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    t0 = time.time()
    _state["calls"] += 1
    try:
        with urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        msg = data["choices"][0]["message"]
        text = strip_think(msg.get("content") or "")
        _state["fails"] = 0
        return dedash(text) or None
    except Exception as e:
        _state["fails"] += 1
        print(f"llm: call failed ({type(e).__name__}: {str(e)[:120]})", flush=True)
        return None
    finally:
        _state["spent"] += time.time() - t0


def parse_json(text):
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                return json.JSONDecoder().raw_decode(text[i:])[0]
            except ValueError:
                continue
    return None


def chat_json(system, user, **kw):
    """Parsed JSON reply (dict or list), or None. String values are de-dashed."""
    obj = parse_json(chat(system + "\n\nReply with a single JSON object and nothing else.", user, **kw))

    def clean(v):
        if isinstance(v, str):
            return dedash(v.strip())
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v
    return clean(obj) if obj is not None else None
