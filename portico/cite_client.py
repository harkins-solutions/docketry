"""CourtListener client for citation verification. Needs the 'cite' extra.

Thin and honest: the user's own token (COURTLISTENER_TOKEN), tight timeouts,
and every network failure surfaces as CiteError — offline degrades to
extraction-only in the CLI, never to a silent pass.
"""
from __future__ import annotations

import os

from .cite import CiteError, Lookup

BASE = "https://www.courtlistener.com/api/rest/v4"


class CourtListenerClient:
    def __init__(self, token: str | None = None, base_url: str = BASE, timeout: float = 30.0):
        try:
            import httpx
        except ImportError:
            raise CiteError(
                "network verification needs the 'cite' extra:"
                " pip install 'portico-legal[cite]'"
            ) from None
        headers = {"User-Agent": "portico-legal cite-verify"}
        token = token or os.environ.get("COURTLISTENER_TOKEN")
        if token:
            headers["Authorization"] = f"Token {token}"
        self._http = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def lookup(self, citation_text: str) -> Lookup:
        try:
            resp = self._http.post("/citation-lookup/", data={"text": citation_text})
            resp.raise_for_status()
            results = resp.json()
        except Exception as e:
            raise CiteError(f"citation lookup failed for {citation_text!r}: {e}") from e
        for row in results:
            if row.get("status") == 200 and row.get("clusters"):
                cluster = row["clusters"][0]
                return Lookup(
                    exists=True,
                    case_name=cluster.get("case_name", ""),
                    cluster_id=cluster.get("id"),
                )
        return Lookup(exists=False)

    def opinion_text(self, cluster_id: int) -> tuple[str, str | None]:
        try:
            resp = self._http.get(
                "/opinions/", params={"cluster__id": cluster_id, "fields": "plain_text,xml_harvard,html_with_citations"}
            )
            resp.raise_for_status()
            rows = resp.json().get("results", [])
        except Exception as e:
            raise CiteError(f"opinion fetch failed for cluster {cluster_id}: {e}") from e
        if not rows:
            return "", None
        op = rows[0]
        html = op.get("xml_harvard") or op.get("html_with_citations") or None
        plain = op.get("plain_text") or ""
        if not plain and html:
            import re
            plain = re.sub(r"<[^>]+>", " ", html)
        return plain, html
