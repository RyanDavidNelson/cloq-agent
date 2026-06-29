"""LLM access via OpenAI-compatible endpoints (works with vLLM and Ollama alike).

We do not wrap a bespoke client: the `openai` SDK speaks to any /v1 server, so a local
Qwen-Coder on the 5090 and an optional cloud frontier model share one code path. The only
policy here is *escalation*: hard goals that burn the local budget get one shot at a stronger
model, mirroring the cost-minimizing hybrid pattern.
"""
from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI

from .config import ModelCfg


@dataclass
class Completion:
    text: str
    model: str
    escalated: bool


class LLM:
    def __init__(self, cfg: ModelCfg):
        self.cfg = cfg
        self._primary = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)
        self._escalation = (
            OpenAI(base_url=cfg.escalation.base_url, api_key=cfg.escalation.api_key or "x")
            if cfg.escalation.enabled
            else None
        )

    def complete(
        self,
        system: str,
        user: str,
        *,
        escalate: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Completion:
        use_esc = escalate and self._escalation is not None
        client = self._escalation if use_esc else self._primary
        model = self.cfg.escalation.name if use_esc else self.cfg.name
        # Some models (e.g. Claude Opus 4.8) reject `temperature` entirely. `cfg.temperature = None`
        # marks such a model: omit the param regardless of any caller override, so the same client
        # works for Ollama (which wants it) and those models (which forbid it).
        temp = None if self.cfg.temperature is None else (
            self.cfg.temperature if temperature is None else temperature)
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self.cfg.max_tokens if max_tokens is None else max_tokens,
        }
        if temp is not None:
            kwargs["temperature"] = temp
        resp = client.chat.completions.create(**kwargs)
        return Completion(
            text=resp.choices[0].message.content or "",
            model=model,
            escalated=use_esc,
        )

    @property
    def can_escalate(self) -> bool:
        return self._escalation is not None

    def healthcheck(self) -> str:
        """Issue a tiny completion to confirm the model server is reachable.

        Returns the model's reply on success. Raises RuntimeError with the endpoint URL and
        model name on any failure, so a misconfigured server is diagnosed *before* a full run
        rather than after a long, doomed loop.
        """
        try:
            resp = self._primary.chat.completions.create(
                model=self.cfg.name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
        except Exception as e:  # noqa: BLE001 - we re-raise with diagnostic context
            raise RuntimeError(
                f"model preflight failed: cannot reach model '{self.cfg.name}' at "
                f"'{self.cfg.base_url}': {type(e).__name__}: {e}"
            ) from e
        return resp.choices[0].message.content or ""
