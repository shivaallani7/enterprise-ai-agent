"""
RAGAS evaluation pipeline.
Loads golden Q&A pairs from Cosmos DB, runs RAGAS metrics,
and returns a pass/fail result with scores.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import yaml
import structlog
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_recall,
    context_precision,
)
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper

logger = structlog.get_logger()
ROOT = Path(__file__).parent


def _build_ragas_llm() -> tuple:
    """Configure RAGAS to use Azure OpenAI instead of OpenAI directly."""
    from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

    llm = LangchainLLMWrapper(
        AzureChatOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            azure_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
            openai_api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-05-01-preview"),
            temperature=0,
        )
    )
    embeddings = LangchainEmbeddingsWrapper(
        AzureOpenAIEmbeddings(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            azure_deployment=os.environ.get(
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large"
            ),
            openai_api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-05-01-preview"),
        )
    )
    return llm, embeddings


def load_thresholds() -> dict:
    with open(ROOT / "thresholds.yaml") as f:
        return yaml.safe_load(f)["metrics"]


async def load_golden_dataset() -> list[dict]:
    """Load golden Q&A pairs from Cosmos DB 'feedback' container where correction != null."""
    from azure.cosmos.aio import CosmosClient

    endpoint = os.environ["COSMOS_ENDPOINT"]
    key = os.environ["COSMOS_KEY"]
    database = os.environ.get("COSMOS_DATABASE", "agent-db")

    async with CosmosClient(url=endpoint, credential=key) as client:
        db = client.get_database_client(database)
        container = db.get_container_client("feedback")
        query = (
            "SELECT c.sessionId, c.messageId, c.originalQuestion, c.correction, c.storyId "
            "FROM c WHERE c.correction != null AND c.correction != '' "
            "ORDER BY c.timestamp DESC OFFSET 0 LIMIT 200"
        )
        items = []
        async for item in container.query_items(query=query):
            items.append(item)
    return items


def _call_agent(question: str, story_id: str | None) -> tuple[str, list[str]]:
    """Synchronously call the chat endpoint for evaluation. Returns (answer, contexts)."""
    import httpx

    api_base = os.environ.get("EVAL_API_BASE", "http://localhost:8000")
    token = os.environ.get("EVAL_API_TOKEN", "dev-token")

    payload = {
        "sessionId": f"eval_{int(time.time())}",
        "storyId": story_id,
        "messages": [{"role": "user", "content": question}],
    }
    # Collect SSE stream
    full_answer = ""
    sources: list[str] = []
    with httpx.Client(timeout=60) as client:
        with client.stream(
            "POST",
            f"{api_base}/api/chat",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    chunk = json.loads(line[5:].strip())
                    full_answer += chunk.get("delta", "")
                    sources.extend(chunk.get("sources", []))

    return full_answer, sources


def build_ragas_dataset(golden: list[dict]) -> Dataset:
    questions, answers, ground_truths, contexts = [], [], [], []

    for item in golden:
        question = item.get("originalQuestion", item.get("messageId", ""))
        ground_truth = item.get("correction", "")
        story_id = item.get("storyId")

        if not question or not ground_truth:
            continue

        answer, ctx = _call_agent(question, story_id)
        questions.append(question)
        answers.append(answer)
        ground_truths.append(ground_truth)
        contexts.append(ctx if ctx else ["No context retrieved"])

    return Dataset.from_dict({
        "question": questions,
        "answer": answers,
        "ground_truth": ground_truths,
        "contexts": contexts,
    })


def run_evaluation(dataset: Dataset, thresholds: dict) -> dict:
    ragas_llm, ragas_embeddings = _build_ragas_llm()
    result = evaluate(
        dataset,
        metrics=[faithfulness, answer_relevancy, context_recall, context_precision],
        llm=ragas_llm,
        embeddings=ragas_embeddings,
    )
    scores = result.to_pandas().mean().to_dict()

    passed = True
    details = {}
    for metric_name, cfg in thresholds.items():
        score = scores.get(metric_name, 0.0)
        threshold = cfg["threshold"]
        ok = score >= threshold
        if not ok:
            passed = False
        details[metric_name] = {
            "score": round(score, 4),
            "threshold": threshold,
            "passed": ok,
        }

    return {"passed": passed, "scores": details}


def format_pr_comment(result: dict, thresholds: dict) -> str:
    rows = []
    for metric, data in result["scores"].items():
        status = "✅" if data["passed"] else "❌"
        rows.append(
            f"| {metric} | {data['score']:.4f} | {data['threshold']} | {status} |"
        )

    summary = "All metrics passed ✅" if result["passed"] else "One or more metrics failed ❌ — merge blocked."
    rows_str = "\n".join(rows)

    tpl = thresholds.get("notifications", {}).get("comment_template", "")
    if tpl:
        return tpl.format(rows=rows_str, summary=summary)

    return f"## RAGAS Results\n\n| Metric | Score | Threshold | Status |\n|--------|-------|-----------|--------|\n{rows_str}\n\n{summary}"


async def main():
    logger.info("Starting RAGAS evaluation")
    thresholds = load_thresholds()

    golden = await load_golden_dataset()
    if not golden:
        logger.warning("No golden dataset items found — skipping evaluation")
        # Write empty result and exit 0 so CI doesn't fail on first run
        print(json.dumps({"passed": True, "scores": {}, "note": "no golden data"}))
        return

    logger.info("Loaded golden dataset", count=len(golden))
    dataset = build_ragas_dataset(golden)
    result = run_evaluation(dataset, thresholds)

    comment = format_pr_comment(result, thresholds)
    print(comment)

    output_path = Path(os.environ.get("RAGAS_OUTPUT", "ragas_result.json"))
    output_path.write_text(json.dumps(result, indent=2))

    if not result["passed"]:
        logger.error("RAGAS evaluation FAILED", scores=result["scores"])
        sys.exit(1)

    logger.info("RAGAS evaluation PASSED", scores=result["scores"])


if __name__ == "__main__":
    asyncio.run(main())
