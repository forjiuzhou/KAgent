"""Job system — lightweight helpers for background task management.

Reads/writes contract and progress Markdown files under `.meta/jobs/`.
All state lives in plain Markdown files so both humans and LLMs can read them.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from noteweaver.constants import (
    JOB_DEFAULT_MAX_ITERATIONS,
    JOB_STALL_THRESHOLD,
    JOB_DIR,
)

if TYPE_CHECKING:
    from noteweaver.vault.core import Vault


# ======================================================================
# Job ID generation
# ======================================================================

def generate_job_id(description: str) -> str:
    """Generate a job ID: slugified description + date + random suffix."""
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", description.lower())
    slug = slug.strip("-")[:30]
    date = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = secrets.token_hex(2)
    return f"{slug}-{date}-{suffix}"


# ======================================================================
# Contract parsing
# ======================================================================

def parse_contract(content: str) -> dict:
    """Parse a contract.md file into structured fields.

    Returns dict with keys: status, goal, criteria, max_iterations, created.
    """
    result: dict = {
        "status": "draft",
        "goal": "",
        "criteria": [],
        "max_iterations": JOB_DEFAULT_MAX_ITERATIONS,
        "created": "",
    }

    current_section = ""
    for line in content.split("\n"):
        stripped = line.strip()

        if stripped.startswith("## Status"):
            current_section = "status"
            continue
        elif stripped.startswith("## Goal"):
            current_section = "goal"
            continue
        elif stripped.startswith("## Acceptance Criteria"):
            current_section = "criteria"
            continue
        elif stripped.startswith("## Max Iterations"):
            current_section = "max_iterations"
            continue
        elif stripped.startswith("## Created"):
            current_section = "created"
            continue
        elif stripped.startswith("## "):
            current_section = ""
            continue

        if current_section == "status" and stripped:
            result["status"] = stripped.lower()
        elif current_section == "goal" and stripped:
            if result["goal"]:
                result["goal"] += " " + stripped
            else:
                result["goal"] = stripped
        elif current_section == "criteria" and stripped.startswith("- "):
            result["criteria"].append(stripped[2:])
        elif current_section == "max_iterations" and stripped:
            try:
                result["max_iterations"] = int(stripped)
            except ValueError:
                pass
        elif current_section == "created" and stripped:
            result["created"] = stripped

    return result


def extract_audit_criteria(criteria: list[str]) -> list[dict]:
    """Extract [audit: ...] tagged criteria from the criteria list.

    Returns list of dicts with keys: text, metric, original.
    """
    audit_criteria = []
    pattern = re.compile(r"\[audit:\s*(.+?)\]")
    for c in criteria:
        m = pattern.search(c)
        if m:
            audit_criteria.append({
                "text": c,
                "metric": m.group(1).strip(),
                "original": c,
            })
    return audit_criteria


def check_audit_criteria(audit_criteria: list[dict], audit_report: dict) -> list[dict]:
    """Check which audit-tagged criteria pass based on audit_report.

    Returns list of dicts with keys: text, metric, passed, detail.
    """
    results = []
    for ac in audit_criteria:
        metric = ac["metric"]
        passed = False
        detail = ""

        if "= 0" in metric or "== 0" in metric:
            field = metric.split("=")[0].strip().replace(" ", "_")
            value = audit_report.get(field)
            if value is not None:
                if isinstance(value, list):
                    passed = len(value) == 0
                    detail = f"{field}: {len(value)}"
                elif isinstance(value, (int, float)):
                    passed = value == 0
                    detail = f"{field}: {value}"
                else:
                    detail = f"{field}: {value}"
            else:
                detail = f"metric '{field}' not found in audit"
        elif ">=" in metric:
            parts = metric.split(">=")
            if len(parts) == 2:
                field = parts[0].strip().replace(" ", "_")
                try:
                    threshold = float(parts[1].strip())
                except ValueError:
                    threshold = 0
                value = audit_report.get(field)
                if isinstance(value, (int, float)):
                    passed = value >= threshold
                    detail = f"{field}: {value} (threshold: {threshold})"
                else:
                    detail = f"metric '{field}': {value}"
        else:
            field = metric.replace(" ", "_")
            value = audit_report.get(field)
            if value is not None:
                detail = f"{field}: {value}"
            else:
                detail = f"metric '{metric}' — manual check needed"

        results.append({
            "text": ac["text"],
            "metric": metric,
            "passed": passed,
            "detail": detail,
        })
    return results


# ======================================================================
# Progress parsing
# ======================================================================

def parse_progress(content: str) -> dict:
    """Parse a progress.md file. Returns dict with iteration_count and last_assessment."""
    iterations = 0
    last_assessment = ""
    declares_complete = False

    for line in content.split("\n"):
        stripped = line.strip()
        if re.match(r"^## Iteration \d+", stripped):
            try:
                n = int(re.search(r"\d+", stripped).group())
                iterations = max(iterations, n)
            except (ValueError, AttributeError):
                pass
        if "完成" in stripped or "complete" in stripped.lower():
            if "建议标记完成" in stripped or "mark.*complete" in stripped.lower():
                declares_complete = True
        if stripped.startswith("### 自评") or stripped.startswith("### Self"):
            last_assessment = ""
        elif last_assessment == "" and stripped and not stripped.startswith("#"):
            last_assessment = stripped

    return {
        "iteration_count": iterations,
        "last_assessment": last_assessment,
        "declares_complete": declares_complete,
    }


# ======================================================================
# Job directory scanning
# ======================================================================

def list_jobs(vault: "Vault") -> list[dict]:
    """Scan .meta/jobs/ and return metadata for all jobs."""
    jobs_dir = vault.root / JOB_DIR
    if not jobs_dir.is_dir():
        return []

    jobs = []
    for job_dir in sorted(jobs_dir.iterdir()):
        if not job_dir.is_dir():
            continue
        contract_path = job_dir / "contract.md"
        if not contract_path.is_file():
            continue

        contract_content = contract_path.read_text(encoding="utf-8")
        contract = parse_contract(contract_content)

        progress_content = ""
        progress_path = job_dir / "progress.md"
        if progress_path.is_file():
            progress_content = progress_path.read_text(encoding="utf-8")

        progress = parse_progress(progress_content) if progress_content else {
            "iteration_count": 0,
            "last_assessment": "",
            "declares_complete": False,
        }

        jobs.append({
            "id": job_dir.name,
            "dir": job_dir,
            "status": contract["status"],
            "goal": contract["goal"],
            "criteria": contract["criteria"],
            "max_iterations": contract["max_iterations"],
            "created": contract["created"],
            "iteration_count": progress["iteration_count"],
            "last_assessment": progress["last_assessment"],
            "declares_complete": progress["declares_complete"],
            "contract_content": contract_content,
            "progress_content": progress_content,
        })

    return jobs


def get_active_jobs(vault: "Vault") -> list[dict]:
    """Return jobs with status 'ready' or 'running'."""
    return [j for j in list_jobs(vault) if j["status"] in ("ready", "running")]


def get_next_ready_job(vault: "Vault") -> dict | None:
    """Return the oldest job with status 'ready', or None."""
    ready = [j for j in list_jobs(vault) if j["status"] == "ready"]
    return ready[0] if ready else None


def get_running_job(vault: "Vault") -> dict | None:
    """Return the current running job, or None."""
    running = [j for j in list_jobs(vault) if j["status"] == "running"]
    return running[0] if running else None


# ======================================================================
# Contract status updates
# ======================================================================

def update_contract_status(job_dir: Path, new_status: str) -> None:
    """Update the Status section of a contract.md file."""
    contract_path = job_dir / "contract.md"
    content = contract_path.read_text(encoding="utf-8")

    lines = content.split("\n")
    new_lines = []
    in_status = False
    status_replaced = False

    for line in lines:
        stripped = line.strip()
        if stripped == "## Status":
            in_status = True
            new_lines.append(line)
            continue

        if in_status and not status_replaced:
            if stripped and not stripped.startswith("##"):
                new_lines.append(new_status)
                status_replaced = True
                continue
            elif stripped.startswith("##"):
                new_lines.append(new_status)
                new_lines.append("")
                status_replaced = True
                in_status = False

        if stripped.startswith("## ") and stripped != "## Status":
            in_status = False

        new_lines.append(line)

    contract_path.write_text("\n".join(new_lines), encoding="utf-8")


# ======================================================================
# Stall detection
# ======================================================================

def detect_stall(job: dict, recent_diffs: list[list[str]]) -> bool:
    """Detect if a job has stalled (no progress for STALL_THRESHOLD iterations).

    recent_diffs: list of file change lists from last N iterations (newest first).
    """
    if len(recent_diffs) < JOB_STALL_THRESHOLD:
        return False
    return all(len(d) == 0 for d in recent_diffs[:JOB_STALL_THRESHOLD])


# ======================================================================
# Worker prompt assembly
# ======================================================================

def build_worker_prompt(
    contract_content: str,
    progress_content: str,
    diff_files: list[str],
    audit_summary: str,
    iteration: int,
) -> str:
    """Assemble the user message for a job worker agent."""
    parts = [
        "# Job Worker — Iteration " + str(iteration),
        "",
        "You are a background worker executing a job contract. "
        "Work through the acceptance criteria systematically. "
        "Use tools to read and write vault pages.",
        "",
        "## Contract",
        "",
        contract_content,
        "",
    ]

    if progress_content:
        last_progress = _extract_last_iterations(progress_content, 2)
        parts.extend([
            "## Recent Progress",
            "",
            last_progress,
            "",
        ])

    if diff_files:
        parts.extend([
            "## Files Changed Last Iteration",
            "",
            "\n".join(f"- {f}" for f in diff_files),
            "",
        ])

    if audit_summary:
        parts.extend([
            "## Current Audit Results",
            "",
            audit_summary,
            "",
        ])

    parts.extend([
        "## Instructions",
        "",
        "1. Review the contract and progress to understand what's done and what remains.",
        "2. Work on the next batch of tasks toward meeting the acceptance criteria.",
        "3. Use read_page before writing. Use search before creating new pages.",
        "4. After completing your work, update the progress file at "
        f"`.meta/jobs/{{job_id}}/progress.md` by appending a new iteration section.",
        "5. In your progress update, include: what you did, file change explanations, "
        "self-assessment, and whether all criteria are met.",
        "6. If all acceptance criteria are met, write "
        "'建议标记完成' in your self-assessment.",
        "7. Only do work described in the contract. "
        "Do not modify .schema/ files unless the contract explicitly declares it.",
        "",
    ])

    return "\n".join(parts)


def _extract_last_iterations(progress_content: str, count: int) -> str:
    """Extract the last N iteration sections from progress.md."""
    sections = re.split(r"(?=^## Iteration \d+)", progress_content, flags=re.MULTILINE)
    iteration_sections = [s for s in sections if s.strip().startswith("## Iteration")]
    if not iteration_sections:
        return progress_content
    return "\n".join(iteration_sections[-count:])


# ======================================================================
# Active jobs summary for main agent context
# ======================================================================

def format_active_jobs_summary(vault: "Vault") -> str:
    """Format active jobs as a summary block for injection into system prompt."""
    active = get_active_jobs(vault)
    if not active:
        return ""

    lines = ["## Active Jobs", ""]
    for job in active:
        status = job["status"]
        iters = job["iteration_count"]
        max_iters = job["max_iterations"]
        goal_short = job["goal"][:100] + ("..." if len(job["goal"]) > 100 else "")
        lines.append(
            f"- [{job['id']}] {goal_short} "
            f"({status}, iteration {iters}/{max_iters})"
        )
        if job["last_assessment"]:
            assessment_short = job["last_assessment"][:150]
            lines.append(f"  Last: {assessment_short}")
    lines.append("")
    return "\n".join(lines)
