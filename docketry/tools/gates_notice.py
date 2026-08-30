"""Notice-parser gate: fail-loud recognition of court notification emails.

Not a match at all is fine (ordinary mail just flows on). A match that cannot
extract a field its adapter declared required is the loud failure: it means
the source changed its template, and the message parks for a human instead of
flowing through with silent holes.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.envelope import Envelope
from ..core.pipeline import Finding, SEVERITY_FAIL, SEVERITY_INFO
from . import notices
from ..core.gates import register


@register
class NoticeParser:
    id = "notice-parser"
    allowed_stages = {"ingest"}

    _cache: tuple[str | None, list] | None = None

    def _adapters(self, options: dict) -> list:
        adapters_file = options.get("adapters_file")
        if adapters_file and not Path(adapters_file).exists():
            raise notices.AdapterError(f"adapters_file not found: {adapters_file}")
        cache = type(self)._cache
        if cache is not None and cache[0] == adapters_file:
            return cache[1]
        stack = notices.stack(adapters_file)
        type(self)._cache = (adapters_file, stack)
        return stack

    def check(self, env: Envelope, options: dict) -> list[Finding]:
        result = notices.parse(env, self._adapters(options))
        if result is None:
            return []
        findings = [
            Finding(
                self.id,
                SEVERITY_INFO,
                f"{result.notice_type} via {result.adapter}: "
                + json.dumps(result.fields, ensure_ascii=False)[:400],
            )
        ]
        for fname in result.missing:
            findings.append(
                Finding(
                    self.id,
                    SEVERITY_FAIL,
                    f"adapter '{result.adapter}' matched but required field"
                    f" '{fname}' did not extract — the source may have changed"
                    " its template",
                )
            )
        return findings
