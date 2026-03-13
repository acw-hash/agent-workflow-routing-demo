from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .config import Settings


@dataclass
class RouteDecision:
    domain: str
    policy_source: str | None
    reason: str


class PolicyRouter:
    def __init__(self, settings: Settings) -> None:
        self._policy_text = self._load_policies(settings)

    @staticmethod
    def _load_policies(settings: Settings) -> dict[str, str]:
        policies: dict[str, str] = {}
        for domain, file_path in settings.policy_files.items():
            path = Path(file_path)
            policies[domain] = path.read_text(encoding="utf-8") if path.exists() else ""
        return policies

    def route(self, message: str) -> RouteDecision:
        lowered = message.lower()

        fraud_keywords = [
            "fraud",
            "unauthorized",
            "stolen",
            "identity theft",
            "compromised",
            "scam",
            "suspicious",
        ]
        refunds_keywords = [
            "refund",
            "dispute",
            "chargeback",
            "merchant",
            "billing error",
            "goods",
            "services",
            "pending refund",
        ]
        card_keywords = [
            "card",
            "activate",
            "declined",
            "pin",
            "replacement",
            "atm",
            "limit",
            "daily spending",
        ]

        if any(word in lowered for word in fraud_keywords):
            return RouteDecision("fraud", "fraud-policies.md", "Matched fraud-related terms.")
        if any(word in lowered for word in refunds_keywords):
            return RouteDecision(
                "refunds_disputes",
                "refunds-and-disputes-policies.md",
                "Matched refunds/disputes terms.",
            )
        if any(word in lowered for word in card_keywords):
            return RouteDecision("card_services", "card-services-policies.md", "Matched card service terms.")

        return RouteDecision("unknown", None, "No policy domain strongly matched.")

    def get_policy_context(self, domain: str, max_chars: int = 3500) -> str:
        source = self._policy_text.get(domain, "")
        return source[:max_chars]

    def fallback_response(self, decision: RouteDecision) -> str:
        if decision.domain == "fraud":
            return (
                "This looks like a fraud-related concern. Please report suspicious or unauthorized activity immediately. "
                "The fraud team reviews cases and can apply protections while the investigation proceeds."
            )
        if decision.domain == "refunds_disputes":
            return (
                "This appears to be a refunds/disputes question. Typical merchant refund timing is 3-10 business days, "
                "and disputes may require transaction details and supporting documents."
            )
        if decision.domain == "card_services":
            return (
                "This appears to be a card-services request. I can help with activation, declined transactions, "
                "replacement timelines, PIN reset guidance, and daily limit questions."
            )

        return (
            "I can help with card services, fraud concerns, or refunds and disputes. "
            "Please share a little more detail so I can route your request correctly."
        )

    def grounded_response(self, decision: RouteDecision, message: str) -> str:
        lowered = message.lower()

        if decision.domain == "card_services":
            if any(term in lowered for term in ["replacement", "replace", "new card", "delivery", "ship", "expedited"]):
                return (
                    "For card replacement delivery: expedited shipping is typically 1-2 business days, and standard shipping is "
                    "typically 5-7 business days. Replacement cards can be issued for lost, stolen, damaged, or compromised cards."
                )

            if "pin" in lowered:
                return (
                    "PIN reset requires identity verification and must be completed through approved secure channels. "
                    "For security, PIN values cannot be shared verbally."
                )

            if any(term in lowered for term in ["declined", "decline", "declined transaction"]):
                return (
                    "Common decline reasons include insufficient balance, exceeded daily limits, merchant restrictions, or suspected fraud. "
                    "Support can confirm visible decline reason, available balance, and limits, and escalate to fraud when needed."
                )

            if any(term in lowered for term in ["limit", "daily", "atm", "withdrawal"]):
                return (
                    "Daily purchase and ATM withdrawal limits are predefined. Temporary limit increases can be requested, "
                    "with approval based on account standing and internal policy."
                )

        if decision.domain == "refunds_disputes":
            if any(term in lowered for term in ["refund", "refund timing", "refund policy", "timeline"]):
                return (
                    "Typical merchant refund timing is 3-10 business days. Banks do not control merchant refund speed. "
                    "If you are past that range, support can help review status and next steps."
                )

            if any(term in lowered for term in ["dispute", "chargeback", "charged incorrectly", "did not receive"]):
                return (
                    "For disputes, provide transaction date and amount, merchant name, dispute reason, and any supporting documents. "
                    "A temporary credit may be issued during review, and final outcome depends on documentation."
                )

        if decision.domain == "fraud":
            if any(term in lowered for term in ["unauthorized", "stolen", "identity", "compromised", "fraud", "scam"]):
                return (
                    "Report suspected fraud immediately. Fraud investigations typically take several business days. "
                    "For confirmed unauthorized transactions, customers are not held liable; provisional credits may be issued during review."
                )

        # If no intent-specific pattern matched, prefer a richer policy summary over a classification-only answer.
        summary = self._top_policy_lines(decision.domain, message)
        if summary:
            return summary
        return self.fallback_response(decision)

    def _top_policy_lines(self, domain: str, message: str, max_lines: int = 3) -> str:
        policy_text = self._policy_text.get(domain, "")
        if not policy_text:
            return ""

        query_terms = {
            token
            for token in re.findall(r"[a-zA-Z]{3,}", message.lower())
            if token not in {"what", "with", "this", "that", "from", "have", "your", "about", "please", "help"}
        }
        if not query_terms:
            return ""

        candidates: list[tuple[int, str]] = []
        for raw_line in policy_text.splitlines():
            line = raw_line.strip().lstrip("- ").strip()
            if not line or line.startswith("#"):
                continue
            words = set(re.findall(r"[a-zA-Z]{3,}", line.lower()))
            score = len(query_terms.intersection(words))
            if score > 0:
                candidates.append((score, line))

        if not candidates:
            return ""

        top_lines = [line for _, line in sorted(candidates, key=lambda item: item[0], reverse=True)[:max_lines]]
        return "Based on policy: " + " ".join(top_lines)
