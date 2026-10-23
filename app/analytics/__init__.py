"""Аналитика для руководителя HR и HR: зоны риска, кластеры тем, воронка этапов, настроение.

- `risks(session, now=None, *, site=None) -> list[RiskItem]` — новички в зоне риска с причинами (правила — rules.py);
- `clusters(session, weeks=4, now=None, *, site=None)` — темы ответов опросов, динамика, обезличенные цитаты;
- `funnel(session, now=None, *, site=None)` — сколько новичков на каждом этапе адаптации;
- `summary(session, now=None, *, site=None)` — всё вместе для GET /api/analytics.
"""

from app.analytics.clusters import clusters
from app.analytics.risks import RiskItem, reason_counts, risks
from app.analytics.rules import Reason
from app.analytics.summary import funnel, mood, summary

__all__ = ["Reason", "RiskItem", "clusters", "funnel", "mood", "reason_counts", "risks", "summary"]
