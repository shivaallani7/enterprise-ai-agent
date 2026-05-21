"""
User profile model with persona support.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class Persona(str, Enum):
    GENERAL = "general"
    FRONTEND_ENGINEER = "frontend_engineer"
    BACKEND_ENGINEER = "backend_engineer"
    SOFTWARE_ENGINEER = "software_engineer"
    TECH_LEAD = "tech_lead"
    QA_ENGINEER = "qa_engineer"
    PRODUCT_OWNER = "product_owner"
    PRODUCT_MANAGER = "product_manager"
    DEVOPS_ENGINEER = "devops_engineer"


PERSONA_LABELS: dict[str, str] = {
    "general":            "General",
    "frontend_engineer":  "Frontend Engineer",
    "backend_engineer":   "Backend Engineer",
    "software_engineer":  "Software Engineer",
    "tech_lead":          "Tech Lead",
    "qa_engineer":        "QA Engineer",
    "product_owner":      "Product Owner",
    "product_manager":    "Product Manager",
    "devops_engineer":    "DevOps Engineer",
}

# System prompt addenda injected per persona
PERSONA_INSTRUCTIONS: dict[str, str] = {
    "general": "",
    "frontend_engineer": (
        "The user is a Frontend Engineer. Prioritise UI/UX code (React, TypeScript, CSS), "
        "component structure, and design-system patterns. When referencing backend code, "
        "focus on the API contract (shapes, endpoints) rather than implementation details."
    ),
    "backend_engineer": (
        "The user is a Backend Engineer. Focus on server-side logic, data models, API design, "
        "database queries, and service integrations. Skip frontend details unless asked."
    ),
    "software_engineer": (
        "The user is a Software Engineer. Provide full-stack context — cover both frontend "
        "and backend perspectives. Include implementation details, code patterns, and "
        "practical examples. Assume solid programming knowledge."
    ),
    "tech_lead": (
        "The user is a Tech Lead. Emphasise architecture trade-offs, code quality, "
        "scalability concerns, and cross-cutting patterns. Include PR review considerations "
        "and highlight technical debt where relevant."
    ),
    "qa_engineer": (
        "The user is a QA Engineer. Focus on testability, edge cases, acceptance criteria gaps, "
        "error handling paths, and regression risks. Suggest test scenarios and point out "
        "untested branches when reviewing code. Highlight what could break and how to verify it."
    ),
    "product_owner": (
        "The user is a Product Owner. Use plain, non-technical language. Translate code and "
        "technical decisions into user-facing impact and business value. Focus on feature "
        "completeness, acceptance criteria, story scope, and what the team has shipped vs "
        "what is still pending. Avoid deep implementation details."
    ),
    "product_manager": (
        "The user is a Product Manager. Use plain language — avoid deep implementation details. "
        "Translate technical concepts into business impact. Focus on feature scope, "
        "story clarity, and acceptance criteria completeness."
    ),
    "devops_engineer": (
        "The user is a DevOps Engineer. Focus on deployment configuration, CI/CD pipelines, "
        "infrastructure-as-code, environment variables, containerisation, and observability "
        "(logs, metrics, traces)."
    ),
}


class UserProfile(BaseModel):
    """Stored in Cosmos DB users container."""
    id: str
    sub: str
    email: str
    name: str
    persona: str = "general"
    created_at: int = 0
    updated_at: int = 0


class UserProfileUpdate(BaseModel):
    """Fields the user can change."""
    name: Optional[str] = None
    persona: Optional[str] = None
