"""Bring your own model — and it has to be one running on your own network.

Docketry's promise is that nothing is copied off the machine. A model changes
that the moment it is reachable over the public internet, so this module does
not take the firm's word for it: `resolve()` refuses any endpoint that is not
loopback or private-range, and refuses it before a single byte of a document
is assembled into a request. Local-only is a check, not a paragraph in the
README.

"Local" means the firm's own network, not strictly the same box: a practice
running a model on a server in the next room is still a practice that has not
sent a client file to a vendor. A public address is refused whatever the
scheme, and no credential is ever read from the manifest.

What a model is allowed to do here is equally narrow. It PROPOSES. It never
decides. Nothing in this module releases a hold, approves anything, applies a
classification, chooses what to redact, or advances a stage — those are the
enforcement points, and they stay deterministic and human-gated. A proposal
carries the endpoint, the model name and a hash of the prompt that produced
it, so any output can be traced back to what generated it.

Off unless configured. Nothing calls it by default.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

DEFAULT_TIMEOUT = 120.0
LOCAL_HOSTNAMES = {"localhost", "ip6-localhost"}


class LLMError(RuntimeError):
    """Configuration or transport failure. Never a silent fallback."""


class RemoteEndpointRefused(LLMError):
    """The configured endpoint is not on the firm's own network."""


@dataclass
class LLMConfig:
    base_url: str
    model: str
    timeout: float = DEFAULT_TIMEOUT


@dataclass
class Proposal:
    """A model's suggestion, and everything needed to attribute it."""
    text: str
    model: str
    endpoint: str
    prompt_sha256: str
    created_at: str
    # Reasoning models (DeepSeek-R1, Qwen3 and friends) narrate before they
    # answer. That narration is kept, but kept SEPARATE: pasted into a case
    # file it reads as the conclusion, and it is the least reliable part of
    # the output — a model talking itself out of its own first answer.
    reasoning: str = ""

    @property
    def provenance(self) -> str:
        return (f"proposed by {self.model} at {self.endpoint}"
                f" (prompt {self.prompt_sha256[:12]}) — a suggestion, not a"
                " finding, and not approved by anyone")


def _is_private(host: str) -> bool:
    """True when every address the host resolves to is loopback or private.

    Resolution matters: a hostname that looks internal can point anywhere, and
    the check has to be about where the packets actually go. If ANY resolved
    address is public the endpoint is refused — a name that resolves to both
    is not a local model, it is a local model plus a way out.
    """
    if host.lower() in LOCAL_HOSTNAMES or host.lower().endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise LLMError(f"cannot resolve {host!r}: {e}") from None
    addrs = {i[4][0] for i in infos}
    if not addrs:
        raise LLMError(f"cannot resolve {host!r}")
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            return False
    return True


def resolve(base_url: str) -> str:
    """Validate an endpoint, or refuse it. Called before any request is built."""
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise LLMError(f"model endpoint must be http or https, got {base_url!r}")
    if not parsed.hostname:
        raise LLMError(f"model endpoint has no host: {base_url!r}")
    if not _is_private(parsed.hostname):
        raise RemoteEndpointRefused(
            f"{parsed.hostname} is not on your network. Docketry runs models"
            " locally only: nothing it reads is sent to a third party, and a"
            " public endpoint would break that. Point base_url at a model on"
            " this machine or your own LAN (Ollama, llama.cpp, vLLM and LM"
            " Studio all serve an OpenAI-compatible API)."
        )
    return base_url.rstrip("/")


def propose(cfg: LLMConfig, prompt: str, *, system: str | None = None) -> Proposal:
    """Ask the local model for a suggestion. Returns a Proposal, never a decision.

    Speaks the OpenAI-compatible chat API, which every local server worth
    running already implements, so "bring your own model" needs one code path
    rather than one per vendor.
    """
    endpoint = resolve(cfg.base_url)
    messages = ([{"role": "system", "content": system}] if system else [])
    messages.append({"role": "user", "content": prompt})
    body = json.dumps({"model": cfg.model, "messages": messages,
                       "stream": False}).encode()
    req = urllib.request.Request(
        f"{endpoint}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "docketry (local model)"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise LLMError(f"local model returned {e.code}: {e.reason}") from None
    except urllib.error.URLError as e:
        raise LLMError(
            f"cannot reach the local model at {endpoint}: {e.reason}."
            " Is it running?"
        ) from None
    except json.JSONDecodeError:
        raise LLMError(f"local model at {endpoint} returned a non-JSON body") from None

    try:
        message = payload["choices"][0]["message"]
        text = message["content"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(
            f"local model at {endpoint} returned no completion —"
            " the server answered but not in the OpenAI-compatible shape"
        ) from None

    # Servers differ: some return reasoning in its own field, others leave
    # <think> blocks inline in the content. Handle both, so a reasoning model
    # is not silently reported as having answered with its own monologue.
    reasoning = (message.get("reasoning_content") or "").strip()
    text, inline = _split_reasoning(text or "")
    reasoning = (reasoning + ("\n\n" if reasoning and inline else "") + inline).strip()
    if not text.strip() and reasoning:
        raise LLMError(
            f"model at {endpoint} returned only reasoning and no answer —"
            " the response is its working, not a proposal"
        )

    return Proposal(
        text=text.strip(),
        reasoning=reasoning,
        model=payload.get("model") or cfg.model,
        endpoint=endpoint,
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


_THINK = re.compile(r"<(think|thinking|reasoning)>(.*?)</\1>", re.S | re.I)


def _split_reasoning(content: str) -> tuple[str, str]:
    """Separate inline <think> narration from the answer itself."""
    blocks = [m.group(2).strip() for m in _THINK.finditer(content)]
    if not blocks:
        return content, ""
    return _THINK.sub("", content), "\n\n".join(blocks)


PROBE_TIMEOUT = 15.0


def probe(cfg: LLMConfig, *, timeout: float = PROBE_TIMEOUT) -> str:
    """One-line health answer for `doctor` and `llm-check`. Never raises.

    Deliberately does NOT use the configured timeout. A health check that
    inherits a two-minute generation budget turns `doctor` — the command you
    run precisely when something is wrong — into the thing that hangs.
    """
    cfg = LLMConfig(base_url=cfg.base_url, model=cfg.model, timeout=timeout)
    try:
        p = propose(cfg, "Reply with the single word: ready")
    except RemoteEndpointRefused as e:
        return f"REFUSED — {e}"
    except LLMError as e:
        return f"unreachable — {e}"
    return f"ready — {p.model} at {p.endpoint}"
