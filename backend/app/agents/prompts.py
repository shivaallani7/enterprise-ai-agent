"""
System prompt builders for story tabs, the general tab, and the Copilot extension.

Why not str.format()
─────────────────────
Jira story fields (description, acceptance criteria, comments) come from
user-written text and frequently contain curly braces — code snippets,
template literals, JSON examples. Passing that text through str.format()
would raise KeyError on every `{variable}` in the story body.

Instead we use str.replace() with unique sentinel tokens that cannot
appear in user content.
"""
from __future__ import annotations

# ── Templates ─────────────────────────────────────────────────────────────────

_STORY_TEMPLATE = """\
You are an expert software engineer assistant for the Fidelity Workplace Investments team.
You are helping with Jira story <<<STORY_KEY>>>: <<<TITLE>>>.

Story context:
<<<DESCRIPTION>>>

Acceptance criteria:
<<<ACCEPTANCE_CRITERIA>>>

Linked PRs: <<<PR_LIST>>>
Recent comments:
<<<COMMENTS>>>

Answer questions specifically about implementing or resolving this story.
Use the code context and documentation tools if you need more detail.
Cite sources (file paths, story fields) in every answer.
Be concise but complete. If you are unsure, say so and suggest what to look up.
"""

_GENERAL_TEMPLATE = """\
You are an expert software engineer assistant for the Fidelity Workplace Investments team.
You have access to the project codebase, documentation, and Jira stories via your tools.

Project context:
<<<PROJECT_CONTEXT>>>

Answer engineering questions using the code search and documentation tools.
Cite sources (file paths, document titles) in every answer.
If a question relates to a specific Jira story, ask the user for the story key so you can
fetch its full context.
"""

_COPILOT_TEMPLATE = """\
You are an expert software engineer assistant embedded in VS Code via GitHub Copilot.
You have deep knowledge of the Fidelity Workplace Investments codebase.

IDE context:
- Repository: <<<REPO_NAME>>>
- Branch: <<<BRANCH>>>
- Active file: <<<ACTIVE_FILE>>>
- Selected code:
```
<<<SELECTION>>>
```

<<<JIRA_CONTEXT>>>
Answer questions about the code in context. Use search tools to find related code and docs.
Cite file paths and line numbers in every answer. Keep answers actionable.
"""


# ── Builders ──────────────────────────────────────────────────────────────────

def _replace(template: str, substitutions: dict[str, str]) -> str:
    """
    Apply substitutions using `<<<TOKEN>>>` sentinels.

    `<<<TOKEN>>>` is chosen because it cannot appear in Jira story text,
    code snippets, or JSON. str.replace() replaces each sentinel exactly once.
    """
    result = template
    for token, value in substitutions.items():
        result = result.replace(f"<<<{token}>>>", value)
    return result


def build_story_prompt(story: dict, persona_instructions: str = "") -> str:
    base = _replace(_STORY_TEMPLATE, {
        "STORY_KEY":           story.get("key", ""),
        "TITLE":               story.get("title", ""),
        "DESCRIPTION":         story.get("description") or "No description provided.",
        "ACCEPTANCE_CRITERIA": story.get("acceptance_criteria") or "None specified.",
        "PR_LIST":             story.get("pr_list") or "None.",
        "COMMENTS":            story.get("comments") or "None.",
    })
    if persona_instructions:
        base += f"\n\nPersona context:\n{persona_instructions}"
    return base


def build_general_prompt(project_context: str = "", persona_instructions: str = "") -> str:
    base = _replace(_GENERAL_TEMPLATE, {
        "PROJECT_CONTEXT": project_context or "No project context loaded yet.",
    })
    if persona_instructions:
        base += f"\n\nPersona context:\n{persona_instructions}"
    return base


def build_copilot_prompt(context: dict, jira_context: str = "") -> str:
    jira_block = f"Jira story context:\n{jira_context}\n" if jira_context else ""
    return _replace(_COPILOT_TEMPLATE, {
        "REPO_NAME":    context.get("repoName", "unknown"),
        "BRANCH":       context.get("branch", "unknown"),
        "ACTIVE_FILE":  context.get("activeFile") or "none",
        "SELECTION":    context.get("selection") or "none",
        "JIRA_CONTEXT": jira_block,
    })
