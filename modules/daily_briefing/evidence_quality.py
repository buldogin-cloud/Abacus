"""Evidence-first quality assessment for collected handoffs.

No external AI calls. The score is a review aid, not a clinical judgment.
"""
from __future__ import annotations

from urllib.parse import urlparse

TRUSTED_HOSTS = {
    "zakon.rada.gov.ua", "moz.gov.ua", "nszu.gov.ua",
    "kmu.gov.ua", "www.kmu.gov.ua",
}
NORMATIVE_SOURCES = {"moz_ukraine", "nszu", "kmu_cabinet"}


def assess(record: dict) -> dict:
    """Return transparent, conservative evidence status and review reasons."""
    source = str(record.get("source") or "").strip()
    title = str(record.get("subject") or record.get("title") or "").strip()
    summary = str(record.get("factual_summary") or record.get("snippet") or "").strip()
    url = str(record.get("url") or record.get("source_url") or "").strip()
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    reasons = []

    if not title:
        reasons.append("missing_title")
    if len(summary) < 40:
        reasons.append("insufficient_context")
    if not record.get("source_id"):
        reasons.append("missing_source_id")

    is_research = record.get("research_kind") in ("clinical", "research")
    if is_research:
        reasons.append("research_content_requires_review")

    is_normative = source in NORMATIVE_SOURCES
    official_link = parsed.scheme == "https" and host in TRUSTED_HOSTS
    verified = record.get("source_verified") is True
    if is_normative and not official_link:
        reasons.append("no_official_primary_link")
    if is_normative and not verified:
        reasons.append("primary_source_not_verified")
    if is_normative and not (record.get("published_at") or record.get("doc_date")):
        reasons.append("missing_publication_date")

    # A collector's claim of verification is not itself proof.
    evidence_status = (
        "verified_claim_requires_review" if verified and official_link
        else "unverified"
    ) if is_normative else "source_recorded"

    # Prevent automated promotion of weak or unverified items.
    needs_review = bool(reasons) or is_normative or is_research
    return {
        "evidence_status": evidence_status,
        "needs_human_review": needs_review,
        "quality_issues": reasons,
        "primary_source_url": url if official_link else "",
        "quality_policy_version": "1",
    }
