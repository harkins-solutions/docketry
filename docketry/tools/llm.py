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
import http.client
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

# The settings live in core.config; this module is the client for them.
from ..core.config import DEFAULT_LLM_TIMEOUT, LLMConfig  # noqa: F401

DEFAULT_TIMEOUT = DEFAULT_LLM_TIMEOUT
DEFAULT_PORTS = {"http": 80, "https": 443}


class LLMError(RuntimeError):
    """Configuration or transport failure. Never a silent fallback."""


class RemoteEndpointRefused(LLMError):
    """The configured endpoint is not on the firm's own network."""


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


@dataclass(frozen=True)
class Endpoint:
    """A vetted endpoint — and the address that was vetted.

    The address travels with it because checking one and connecting to another
    is not a check. Between the lookup and the socket a name is free to answer
    differently, so what was approved is what gets dialled.
    """
    url: str
    scheme: str
    host: str
    port: int
    ip: str


def _addresses(host: str) -> list[str]:
    """Every address a host resolves to, in the order the resolver gave them.

    No name is trusted on its face — not `.local`, not `localhost`. A name
    that looks internal is still just a name, and any DNS server is free to
    answer it with a public address.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise LLMError(f"cannot resolve {host!r}: {e}") from None
    addrs: list[str] = []
    for info in infos:
        a = info[4][0].split("%")[0]
        if a not in addrs:
            addrs.append(a)
    if not addrs:
        raise LLMError(f"cannot resolve {host!r}")
    return addrs


def vet(base_url: str) -> Endpoint:
    """Validate an endpoint, or refuse it. Called before any request is built."""
    parsed = urlparse(base_url)
    if parsed.scheme not in DEFAULT_PORTS:
        raise LLMError(f"model endpoint must be http or https, got {base_url!r}")
    if not parsed.hostname:
        raise LLMError(f"model endpoint has no host: {base_url!r}")
    # EVERY address has to be local. A name that resolves to both a private
    # and a public address is not a local model, it is a local model plus a
    # way out.
    addrs = _addresses(parsed.hostname)
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            raise RemoteEndpointRefused(
                f"{parsed.hostname} is not on your network ({a}). Docketry runs"
                " models locally only: nothing it reads is sent to a third"
                " party, and a public endpoint would break that. Point base_url"
                " at a model on this machine or your own LAN (Ollama,"
                " llama.cpp, vLLM and LM Studio all serve an OpenAI-compatible"
                " API)."
            )
    return Endpoint(
        url=base_url.rstrip("/"),
        scheme=parsed.scheme,
        host=parsed.hostname,
        port=parsed.port or DEFAULT_PORTS[parsed.scheme],
        ip=addrs[0],
    )


def resolve(base_url: str) -> str:
    """The vetted endpoint as a string, for callers that only want the URL."""
    return vet(base_url).url


def _post(ep: Endpoint, path: str, body: bytes, timeout: float) -> bytes:
    """POST to the address that was vetted, and follow nothing.

    Two properties this buys that a plain urlopen does not. The connection
    goes to `ep.ip`, so the address checked is the address dialled rather than
    whatever a second lookup returns. And a 3xx is refused instead of
    followed: a compliant private endpoint that answers with a redirect is a
    way off the network, which is the one thing this module exists to prevent.
    """
    cls = (http.client.HTTPSConnection if ep.scheme == "https"
           else http.client.HTTPConnection)
    conn = cls(ep.host, ep.port, timeout=timeout)
    # Keep host for SNI and the Host header; dial the vetted address.
    dial = conn._create_connection
    conn._create_connection = (
        lambda address, t=None, src=None: dial((ep.ip, address[1]), t, src))
    try:
        conn.request("POST", path, body=body,
                     headers={"Content-Type": "application/json",
                              "User-Agent": "docketry (local model)"})
        resp = conn.getresponse()
        status, reason = resp.status, resp.reason
        location = resp.getheader("Location", "")
        payload = resp.read()
    except (OSError, http.client.HTTPException) as e:
        raise LLMError(
            f"cannot reach the local model at {ep.url}: {e}. Is it running?"
        ) from None
    finally:
        conn.close()
    if 300 <= status < 400:
        raise RemoteEndpointRefused(
            f"the model at {ep.url} answered {status} redirecting to"
            f" {location or 'elsewhere'}. Docketry does not follow redirects"
            " from a model endpoint: the address it vetted is the only one it"
            " will talk to, and a redirect is a way off your network."
        )
    if status >= 400:
        raise LLMError(f"local model returned {status}: {reason}")
    return payload


def propose(cfg: LLMConfig, prompt: str, *, system: str | None = None) -> Proposal:
    """Ask the local model for a suggestion. Returns a Proposal, never a decision.

    Speaks the OpenAI-compatible chat API, which every local server worth
    running already implements, so "bring your own model" needs one code path
    rather than one per vendor.
    """
    ep = vet(cfg.base_url)
    endpoint = ep.url
    messages = ([{"role": "system", "content": system}] if system else [])
    messages.append({"role": "user", "content": prompt})
    body = json.dumps({"model": cfg.model, "messages": messages,
                       "stream": False}).encode()
    raw = _post(ep, _path(ep) + "/v1/chat/completions", body, cfg.timeout)
    try:
        payload = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
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


def _path(ep: Endpoint) -> str:
    """Any path prefix the firm put in base_url (e.g. /openai on LM Studio)."""
    return urlparse(ep.url).path.rstrip("/")


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
