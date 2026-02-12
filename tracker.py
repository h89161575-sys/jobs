#!/usr/bin/env python3
"""Track changes on the Erste Group jobs page and notify Discord."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

TARGET_URL = (
    "https://www.erstegroup.com/de/karriere/stellenangebote"
    "#/joblist/location/Wien/discipline_items/Legal%20%2F%20Compliance%20%2F%20Audit"
)
STATE_PATH = Path("state/erstegroup_wien_legal_jobs.json")
PAGE_TIMEOUT_MS = 60_000

TITLE_KEYS = (
    "title",
    "jobtitle",
    "job_title",
    "position",
    "name",
    "postingtitle",
)
LOCATION_KEYS = ("location", "city", "office", "worklocation")
LINK_KEYS = ("url", "link", "applyurl", "joburl", "detailurl")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_whitespace(value: str) -> str:
    value = value.replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def set_github_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return

    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        return None


def save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
        handle.write("\n")


def _extract_string(record: dict[str, Any], key_patterns: tuple[str, ...]) -> str | None:
    lowered = {str(k).lower(): v for k, v in record.items()}

    for pattern in key_patterns:
        for key, value in lowered.items():
            if pattern in key and isinstance(value, str):
                cleaned = re.sub(r"\s+", " ", value).strip()
                if cleaned:
                    return cleaned
    return None


def _collect_jobs_from_json(value: Any, results: list[dict[str, str]]) -> None:
    if isinstance(value, dict):
        title = _extract_string(value, TITLE_KEYS)
        if title:
            location = _extract_string(value, LOCATION_KEYS) or ""
            link = _extract_string(value, LINK_KEYS) or ""
            results.append({"title": title, "location": location, "href": link})

        for nested in value.values():
            _collect_jobs_from_json(nested, results)
        return

    if isinstance(value, list):
        for item in value:
            _collect_jobs_from_json(item, results)


def _filter_jobs(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    blocked_words = {
        "impressum",
        "datenschutz",
        "cookie",
        "facebook",
        "instagram",
        "linkedin",
        "youtube",
        "kontakt",
        "karriere",
        "stellenangebote",
    }

    seen: set[str] = set()
    filtered: list[dict[str, str]] = []

    for entry in entries:
        title = re.sub(r"\s+", " ", (entry.get("title") or "")).strip()
        href = (entry.get("href") or "").strip()
        location = re.sub(r"\s+", " ", (entry.get("location") or "")).strip()

        if len(title) < 4 or len(title) > 160:
            continue

        title_lower = title.lower()
        if any(word in title_lower for word in blocked_words):
            continue

        if href:
            href = urljoin(TARGET_URL, href)

        key = f"{title}|{href}|{location}"
        if key in seen:
            continue
        seen.add(key)

        filtered.append({"title": title, "href": href, "location": location})

    filtered.sort(key=lambda item: (item["title"].lower(), item["location"].lower(), item["href"]))
    return filtered


def capture_page_snapshot() -> tuple[str, list[dict[str, str]]]:
    response_jobs: list[dict[str, str]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(locale="de-AT")
        page = context.new_page()

        def on_response(response: Any) -> None:
            try:
                content_type = response.headers.get("content-type", "").lower()
                if "json" not in content_type:
                    return

                lowered_url = response.url.lower()
                if not any(token in lowered_url for token in ("job", "career", "stellen", "vacan")):
                    return

                payload = response.json()
            except Exception:
                return

            _collect_jobs_from_json(payload, response_jobs)

        page.on("response", on_response)

        page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)

        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except PlaywrightTimeoutError:
            pass

        page.wait_for_timeout(6_000)

        body_text = page.evaluate("() => document.body ? document.body.innerText : ''")

        dom_candidates = page.evaluate(
            """
            () => {
              const selectors = [
                '[data-testid*="job"] a[href]',
                '[class*="job"] a[href]',
                '[class*="position"] a[href]',
                'article a[href]',
                'li a[href]',
                'a[href]'
              ];

              const result = [];
              const seen = new Set();

              for (const selector of selectors) {
                for (const anchor of document.querySelectorAll(selector)) {
                  const title = (anchor.textContent || '').replace(/\\s+/g, ' ').trim();
                  const href = anchor.getAttribute('href') || anchor.href || '';
                  const scope = (anchor.closest('li,article,section,div')?.innerText || '')
                    .replace(/\\s+/g, ' ')
                    .trim()
                    .slice(0, 350);

                  if (!title) {
                    continue;
                  }

                  const key = `${title}|${href}|${scope}`;
                  if (seen.has(key)) {
                    continue;
                  }
                  seen.add(key);

                  result.push({ title, href, location: scope });
                }
              }

              return result;
            }
            """
        )

        browser.close()

    combined = response_jobs + dom_candidates
    filtered = _filter_jobs(combined)
    return normalize_whitespace(body_text), filtered


def build_state(page_text: str, jobs: list[dict[str, str]]) -> dict[str, Any]:
    page_hash = sha256_text(page_text)

    comparison_mode = "jobs"
    comparison_value: Any = jobs

    if not jobs:
        comparison_mode = "page_hash"
        comparison_value = page_hash

    return {
        "updated_at": now_iso(),
        "url": TARGET_URL,
        "comparison_mode": comparison_mode,
        "comparison_value": comparison_value,
        "page_text_hash": page_hash,
        "job_count": len(jobs),
        "job_candidates": jobs,
    }


def has_changed(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if previous is None:
        return True

    return (
        previous.get("comparison_mode") != current.get("comparison_mode")
        or previous.get("comparison_value") != current.get("comparison_value")
    )


def diff_jobs(previous: dict[str, Any] | None, current: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if previous is None:
        return [], []

    old_jobs = previous.get("job_candidates") or []
    new_jobs = current.get("job_candidates") or []

    old_map = {
        f"{entry.get('title', '')}|{entry.get('href', '')}|{entry.get('location', '')}": entry
        for entry in old_jobs
        if isinstance(entry, dict)
    }
    new_map = {
        f"{entry.get('title', '')}|{entry.get('href', '')}|{entry.get('location', '')}": entry
        for entry in new_jobs
        if isinstance(entry, dict)
    }

    added = [new_map[key] for key in sorted(new_map.keys() - old_map.keys())]
    removed = [old_map[key] for key in sorted(old_map.keys() - new_map.keys())]
    return added, removed


def send_discord_notification(
    webhook_url: str,
    current: dict[str, Any],
    added: list[dict[str, str]],
    removed: list[dict[str, str]],
) -> None:
    lines = [
        "Erste Group tracker: change detected.",
        f"URL: {TARGET_URL}",
        f"UTC: {now_iso()}",
        f"Detected jobs: {current.get('job_count', 0)}",
    ]

    if added:
        lines.append("")
        lines.append(f"Added ({len(added)}):")
        for entry in added[:8]:
            title = entry.get("title", "(no title)")
            href = entry.get("href", "")
            lines.append(f"+ {title} | {href}")

    if removed:
        lines.append("")
        lines.append(f"Removed ({len(removed)}):")
        for entry in removed[:8]:
            title = entry.get("title", "(no title)")
            href = entry.get("href", "")
            lines.append(f"- {title} | {href}")

    if not added and not removed:
        lines.append("")
        lines.append("Tracked content changed, but no clear job-level diff was extracted.")

    content = "\n".join(lines)
    payload = {"content": content[:1900]}

    response = requests.post(webhook_url, json=payload, timeout=20)
    response.raise_for_status()


def main() -> int:
    try:
        page_text, jobs = capture_page_snapshot()
    except Exception as exc:
        print(f"ERROR: could not capture target page: {exc}", file=sys.stderr)
        set_github_output("changed", "false")
        return 1

    current_state = build_state(page_text, jobs)
    previous_state = load_state(STATE_PATH)
    changed = has_changed(previous_state, current_state)

    set_github_output("changed", "true" if changed else "false")
    set_github_output("job_count", str(current_state.get("job_count", 0)))

    if not changed:
        print("No change detected.")
        return 0

    save_state(STATE_PATH, current_state)
    print("Change detected. State file updated.")

    if previous_state is None:
        print("Initial baseline created. Discord notification skipped.")
        return 0

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL is not set. Skipping Discord notification.")
        return 0

    try:
        added, removed = diff_jobs(previous_state, current_state)
        send_discord_notification(webhook_url, current_state, added, removed)
        print("Discord notification sent.")
    except Exception as exc:
        print(f"ERROR: failed to send Discord notification: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
