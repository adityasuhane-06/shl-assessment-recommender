"""
Smoke-test the deployed SHL recommender API.

Usage:
    python smoke_deployed.py

Optional direct OpenRouter check:
    set OPENROUTER_API_KEY=sk-or-v1-...
    python smoke_deployed.py --openrouter

Override target:
    set BASE_URL=https://your-space.hf.space
    python smoke_deployed.py
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE_URL = os.getenv(
    "BASE_URL",
    "https://adityasuhane01-shl-assessment-recommender.hf.space",
).rstrip("/")
CATALOG_PATH = Path("shl_product_catalog.json")


def load_catalog_urls() -> set[str]:
    if not CATALOG_PATH.exists():
        return set()

    with CATALOG_PATH.open(encoding="utf-8") as f:
        catalog = json.loads(f.read(), strict=False)
    return {item["link"] for item in catalog if item.get("link")}


def request_json(url: str, payload: dict | None = None, headers: dict | None = None) -> tuple[int, dict, float]:
    data = None
    final_headers = headers or {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        final_headers = {"Content-Type": "application/json", **final_headers}

    req = Request(url, data=data, headers=final_headers, method="POST" if payload is not None else "GET")
    start = time.perf_counter()
    with urlopen(req, timeout=35) as resp:
        raw = resp.read().decode("utf-8")
        elapsed_ms = (time.perf_counter() - start) * 1000
        return resp.status, json.loads(raw), elapsed_ms


def print_json(title: str, value: dict) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(value, indent=2, ensure_ascii=False))


def validate_response(body: dict, expect_recommendations: bool | None, catalog_urls: set[str]) -> list[str]:
    errors: list[str] = []
    required_keys = {"reply", "recommendations", "end_of_conversation"}
    missing = required_keys - set(body)
    if missing:
        errors.append(f"missing keys: {sorted(missing)}")
        return errors

    if not isinstance(body["reply"], str) or not body["reply"].strip():
        errors.append("reply is empty or not a string")
    if not isinstance(body["recommendations"], list):
        errors.append("recommendations is not a list")
        return errors
    if not isinstance(body["end_of_conversation"], bool):
        errors.append("end_of_conversation is not boolean")

    recommendations = body["recommendations"]
    if expect_recommendations is True and not (1 <= len(recommendations) <= 10):
        errors.append(f"expected 1-10 recommendations, got {len(recommendations)}")
    if expect_recommendations is False and recommendations:
        errors.append(f"expected no recommendations, got {len(recommendations)}")
    if len(recommendations) > 10:
        errors.append(f"too many recommendations: {len(recommendations)}")

    for i, rec in enumerate(recommendations, 1):
        for key in ("name", "url", "test_type"):
            if key not in rec:
                errors.append(f"recommendation {i} missing {key}")
        url = rec.get("url")
        if catalog_urls and url not in catalog_urls:
            errors.append(f"recommendation {i} URL not in catalog: {url}")

    if "trouble reaching the language model" in body.get("reply", "").lower():
        errors.append("LLM fallback was used")

    return errors


def test_health() -> tuple[bool, float]:
    try:
        status, body, elapsed_ms = request_json(f"{BASE_URL}/health")
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"HEALTH FAIL: {exc}")
        return False, 0.0

    print_json(f"Health ({elapsed_ms:.0f} ms)", body)
    return status == 200 and body == {"status": "ok"}, elapsed_ms


TEST_CASES = [
    {
        "name": "Detailed Java Recommendation",
        "expect_recommendations": True,
        "messages": [
            {
                "role": "user",
                "content": "I am hiring a Java developer who works with stakeholders. Mid-level, around 4 years.",
            }
        ],
    },
    {
        "name": "Vague Query Clarification",
        "expect_recommendations": False,
        "messages": [{"role": "user", "content": "I need an assessment."}],
    },
    {
        "name": "Comparison",
        "expect_recommendations": False,
        "messages": [{"role": "user", "content": "What is the difference between OPQ and GSA?"}],
    },
    {
        "name": "Refinement",
        "expect_recommendations": True,
        "messages": [
            {
                "role": "user",
                "content": "We need assessments for an admin assistant who uses Excel and Word.",
            },
            {
                "role": "assistant",
                "content": (
                    "Recommended shortlist: Microsoft Excel 365 (New), Microsoft Word 365 (New), "
                    "MS Excel (New), MS Word (New)."
                ),
            },
            {"role": "user", "content": "Actually add personality tests too."},
        ],
    },
    {
        "name": "Off-topic Refusal",
        "expect_recommendations": False,
        "messages": [{"role": "user", "content": "Can you write a salary negotiation policy for us?"}],
    },
]


def test_chat_case(case: dict, catalog_urls: set[str], show_full: bool) -> tuple[bool, float]:
    payload = {"messages": case["messages"]}

    try:
        status, body, elapsed_ms = request_json(f"{BASE_URL}/chat", payload)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"\n=== {case['name']} ===")
        print(f"HTTP FAIL {exc.code}: {detail}")
        return False, 0.0
    except (URLError, TimeoutError) as exc:
        print(f"\n=== {case['name']} ===")
        print(f"REQUEST FAIL: {exc}")
        return False, 0.0

    errors = []
    if status != 200:
        errors.append(f"status {status}")
    errors.extend(validate_response(body, case["expect_recommendations"], catalog_urls))

    print(f"\n=== {case['name']} ({elapsed_ms:.0f} ms) ===")
    print(f"reply: {body.get('reply', '')}")
    print(f"recommendations: {len(body.get('recommendations', []))}")
    for rec in body.get("recommendations", []):
        print(f"- {rec.get('name')} | {rec.get('test_type')} | {rec.get('url')}")

    if show_full:
        print_json("Raw response", body)

    if errors:
        print("FAIL:")
        for error in errors:
            print(f"- {error}")
    else:
        print("PASS")

    return not errors, elapsed_ms


def test_openrouter() -> bool:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("\nOPENROUTER SKIP: OPENROUTER_API_KEY is not set in this terminal.")
        return False

    payload = {
        "model": os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free"),
        "messages": [{"role": "user", "content": "Say ok"}],
        "max_tokens": 20,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": BASE_URL,
        "X-Title": "SHL Agent",
    }

    try:
        status, body, elapsed_ms = request_json("https://openrouter.ai/api/v1/chat/completions", payload, headers)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"\nOPENROUTER FAIL: HTTP {exc.code}")
        print(detail)
        return False
    except (URLError, TimeoutError) as exc:
        print(f"\nOPENROUTER FAIL: {exc}")
        return False

    print_json(f"OpenRouter ({elapsed_ms:.0f} ms)", body)
    return status == 200 and bool(body.get("choices"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openrouter", action="store_true", help="also test the OpenRouter key in this terminal")
    parser.add_argument("--full", action="store_true", help="print full JSON responses")
    args = parser.parse_args()

    print(f"Testing deployed API: {BASE_URL}")
    catalog_urls = load_catalog_urls()
    print(f"Loaded catalog URLs: {len(catalog_urls)}")

    timings: list[float] = []
    health_ok, health_ms = test_health()
    if health_ms:
        timings.append(health_ms)

    case_results = []
    for case in TEST_CASES:
        ok, elapsed_ms = test_chat_case(case, catalog_urls, args.full)
        case_results.append(ok)
        if elapsed_ms:
            timings.append(elapsed_ms)

    openrouter_ok = True
    if args.openrouter:
        openrouter_ok = test_openrouter()

    print("\n=== Summary ===")
    print(f"health: {'PASS' if health_ok else 'FAIL'}")
    print(f"chat cases: {sum(case_results)}/{len(case_results)} passed")
    if timings:
        print(f"latency avg: {statistics.mean(timings):.0f} ms")
        print(f"latency max: {max(timings):.0f} ms")
    if args.openrouter:
        print(f"direct OpenRouter: {'PASS' if openrouter_ok else 'FAIL'}")

    all_ok = health_ok and all(case_results) and openrouter_ok
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
