from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import os
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

CONFIG_PATH = Path(os.environ.get("BRAIN_CAPTURE_CONFIG", "~/.config/brain-capture/config.yaml")).expanduser()

# V1: hard allow-list. Config can override, but we always intersect with this.
DEFAULT_ALLOWED_FOLDERS = [
    "_review",
    "articles",
    "ai-summaries",
    "prompts",
    "frameworks",
    "customers",
    "vendors",
    "meetings",
    "decisions",
    "references",
    "playbooks",
    "ideas",
    "quotes",
]


@dataclass(frozen=True)
class Config:
    vault_path: Path
    review_threshold: float
    allowed_folders: List[str]
    log_dir: Path
    openai_model: str
    openai_timeout_seconds: int
    openai_max_output_tokens: int
    url_timeout_seconds: int
    url_max_bytes: int
    git_enabled: bool
    git_auto_commit: bool


def alfred_menu_json() -> str:
    items = [
        {
            "title": "Capture clipboard",
            "subtitle": "Process clipboard (text or a single URL) and save Markdown to your vault",
            "arg": "capture",
            "uid": "brain-capture.capture",
        },
        {
            "title": "Open vault folder",
            "subtitle": "Open vault_path in Finder",
            "arg": "open-vault",
            "uid": "brain-capture.open-vault",
        },
        {
            "title": "Open config file",
            "subtitle": "Open ~/.config/brain-capture/config.yaml",
            "arg": "open-config",
            "uid": "brain-capture.open-config",
        },
        {
            "title": "Run health check",
            "subtitle": "Verify config, vault, OpenAI key, deps, and Git (if enabled)",
            "arg": "health-check",
            "uid": "brain-capture.health-check",
        },
    ]
    payload = {"skipknowledge": True, "items": items}
    return json.dumps(payload)


def run_action(action: str) -> str:
    if action == "open-config":
        _ensure_config_file_exists()
        _open_path(CONFIG_PATH)
        return "Opened config file"

    if action == "health-check":
        msg = health_check()
        return msg

    cfg = load_config()

    if action == "open-vault":
        _open_path(cfg.vault_path)
        return "Opened vault folder"

    if action == "capture":
        msg = capture_clipboard(cfg)
        return msg

    raise ValueError(f"Unknown action: {action}")


def _ensure_config_file_exists() -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        return

    # Starter config: safe defaults + placeholders.
    starter = "\n".join(
        [
            f'vault_path: "{Path("~/BrainVault").expanduser()}"',
            "review_threshold: 0.7",
            "allowed_folders:",
            *[f"  - {f}" for f in DEFAULT_ALLOWED_FOLDERS],
            'log_dir: ".brain-capture/logs"',
            "openai:",
            '  model: "gpt-5.2"',
            "  timeout_seconds: 60",
            "  max_output_tokens: 2000",
            "url_fetch:",
            "  timeout_seconds: 20",
            "  max_bytes: 2000000",
            "git:",
            "  enabled: false",
            "  auto_commit: false",
            "",
        ]
    )
    CONFIG_PATH.write_text(starter, encoding="utf-8")


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            f"Missing config file at {CONFIG_PATH}. Run 'br' → 'Open config file' to create it."
        )

    try:
        import yaml  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency PyYAML. Install with: python3 -m pip install -r requirements.txt"
        ) from e

    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RuntimeError("Config must be a YAML mapping/object.")

    vault_path = Path(str(raw.get("vault_path", ""))).expanduser()
    if not str(vault_path):
        raise RuntimeError("Config missing vault_path.")

    allowed = raw.get("allowed_folders", DEFAULT_ALLOWED_FOLDERS)
    if not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed):
        raise RuntimeError("Config allowed_folders must be a list of strings.")
    # Always intersect with v1 allow-list; never expand beyond it.
    allowed_folders = [f for f in allowed if f in set(DEFAULT_ALLOWED_FOLDERS)]
    if "_review" not in allowed_folders:
        allowed_folders = ["_review"] + allowed_folders

    openai_raw = raw.get("openai", {}) or {}
    url_raw = raw.get("url_fetch", {}) or {}
    git_raw = raw.get("git", {}) or {}

    def _int(name: str, default: int) -> int:
        v = raw.get(name, default)
        return int(v) if isinstance(v, (int, float, str)) and str(v).strip() else default

    review_threshold = float(raw.get("review_threshold", 0.7))
    log_dir = Path(str(raw.get("log_dir", ".brain-capture/logs"))).expanduser()
    if not log_dir.is_absolute():
        log_dir = (vault_path / log_dir).resolve()

    return Config(
        vault_path=vault_path,
        review_threshold=review_threshold,
        allowed_folders=allowed_folders,
        log_dir=log_dir,
        openai_model=str(openai_raw.get("model", "gpt-5.2")),
        openai_timeout_seconds=int(openai_raw.get("timeout_seconds", 60)),
        openai_max_output_tokens=int(openai_raw.get("max_output_tokens", 2000)),
        url_timeout_seconds=int(url_raw.get("timeout_seconds", 20)),
        url_max_bytes=int(url_raw.get("max_bytes", 2_000_000)),
        git_enabled=bool(git_raw.get("enabled", False)),
        git_auto_commit=bool(git_raw.get("auto_commit", False)),
    )


def health_check() -> str:
    problems: List[str] = []
    warnings: List[str] = []

    if not CONFIG_PATH.exists():
        problems.append(f"Missing config: {CONFIG_PATH}")
        # Still continue checking environment basics.

    missing_deps = _missing_python_deps()
    if missing_deps:
        problems.append("Missing Python deps: " + ", ".join(missing_deps))

    cfg: Config | None = None
    if CONFIG_PATH.exists() and "yaml" not in missing_deps:
        try:
            cfg = load_config()
        except Exception as e:  # noqa: BLE001
            problems.append(f"Config invalid: {e}")

    if cfg is not None:
        if not cfg.vault_path.exists():
            problems.append(f"vault_path does not exist: {cfg.vault_path}")
        elif not cfg.vault_path.is_dir():
            problems.append(f"vault_path is not a directory: {cfg.vault_path}")
        elif not os.access(cfg.vault_path, os.W_OK):
            problems.append(f"vault_path is not writable: {cfg.vault_path}")

    if not os.environ.get("OPENAI_API_KEY"):
        problems.append("OPENAI_API_KEY is not set in the environment")

    if cfg is not None:
        cfg.log_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(cfg.log_dir, os.W_OK):
            problems.append(f"log_dir is not writable: {cfg.log_dir}")

        if cfg.git_enabled:
            if not (cfg.vault_path / ".git").exists():
                warnings.append("Git enabled, but vault_path does not look like a Git repo")
            else:
                try:
                    r = subprocess.run(
                        ["git", "-C", str(cfg.vault_path), "status", "--porcelain"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if r.returncode != 0:
                        problems.append(f"git status failed: {r.stderr.strip() or r.stdout.strip()}")
                except FileNotFoundError:
                    problems.append("Git enabled, but `git` is not installed / not on PATH")

    if problems:
        out = ["Health check failed:"] + [f"- {p}" for p in problems]
        if warnings:
            out += ["Warnings:"] + [f"- {w}" for w in warnings]
        return "\n".join(out)

    if warnings:
        out = ["Health check OK (with warnings):"] + [f"- {w}" for w in warnings]
        return "\n".join(out)

    return "Health check OK"


def _missing_python_deps() -> List[str]:
    missing: List[str] = []
    for name in ["requests", "yaml", "jsonschema", "bs4", "markdownify"]:
        try:
            __import__(name)
        except Exception:  # noqa: BLE001
            missing.append(name)
    return missing


def capture_clipboard(cfg: Config) -> str:
    clip = _read_clipboard_text()
    if clip is None:
        return "Error: Clipboard is empty."

    clip_stripped = clip.strip()
    if not clip_stripped:
        # Try to differentiate “empty text” vs non-text clipboard.
        info = _clipboard_info()
        if info and "text" not in info.lower() and "utf" not in info.lower():
            return "Error: Clipboard has unsupported content (non-text)."
        return "Error: Clipboard is empty."

    if _is_single_http_url(clip_stripped):
        return _capture_url(cfg, clip_stripped)

    return _capture_text(cfg, clip_stripped)


def _read_clipboard_text() -> str | None:
    try:
        r = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_CTYPE": "UTF-8"},
        )
    except FileNotFoundError:
        raise RuntimeError("pbpaste not found (this workflow is macOS-only).")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "Failed to read clipboard via pbpaste.")
    return r.stdout


def _clipboard_info() -> str | None:
    try:
        r = subprocess.run(
            ["/usr/bin/osascript", "-e", "clipboard info"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip()


_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def _is_single_http_url(s: str) -> bool:
    if not _URL_RE.match(s):
        return False
    try:
        u = urlparse(s)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    if not u.netloc:
        return False
    return True


def _capture_text(cfg: Config, text: str) -> str:
    _ensure_vault_scaffold(cfg)
    candidates = _list_append_candidates(cfg)
    ai_instructions, ai_input = _build_ai_prompt(
        cfg=cfg,
        capture_kind="text",
        source_url="",
        source_title="",
        source_markdown="",
        source_text=text,
        candidate_append_targets=candidates,
    )
    result = _call_openai_structured(cfg, ai_instructions, ai_input, append_candidates=candidates)
    return _apply_ai_result(cfg, result, source_url="")


def _capture_url(cfg: Config, url: str) -> str:
    _ensure_vault_scaffold(cfg)

    html, final_url = _fetch_url_safely(cfg, url)
    meta_title, article_html = _extract_readable_html(html)
    article_md = _html_to_markdown(article_html)

    candidates = _list_append_candidates(cfg)
    ai_instructions, ai_input = _build_ai_prompt(
        cfg=cfg,
        capture_kind="url",
        source_url=final_url,
        source_title=meta_title,
        source_markdown=article_md,
        source_text="",
        candidate_append_targets=candidates,
    )
    result = _call_openai_structured(cfg, ai_instructions, ai_input, append_candidates=candidates)
    return _apply_ai_result(cfg, result, source_url=final_url)


def _ensure_vault_scaffold(cfg: Config) -> None:
    if not cfg.vault_path.exists():
        raise RuntimeError(f"vault_path does not exist: {cfg.vault_path}")
    if not cfg.vault_path.is_dir():
        raise RuntimeError(f"vault_path is not a directory: {cfg.vault_path}")

    # Ensure allowed folders exist (including _review).
    for folder in cfg.allowed_folders:
        (cfg.vault_path / folder).mkdir(parents=True, exist_ok=True)


def _list_append_candidates(cfg: Config) -> List[str]:
    """
    V1: Keep this small and safe.
    We only offer append candidates for a subset of folders where appending is common.
    Returned paths are vault-relative POSIX-ish strings (folder/filename.md).
    """
    append_ok_folders = {"meetings", "decisions", "playbooks", "prompts", "ai-summaries"}
    candidates: List[str] = []
    for folder in cfg.allowed_folders:
        if folder not in append_ok_folders:
            continue
        base = cfg.vault_path / folder
        if not base.exists():
            continue
        for p in sorted(base.glob("*.md")):
            rel = p.relative_to(cfg.vault_path).as_posix()
            candidates.append(rel)
            if len(candidates) >= 200:
                return candidates
    return candidates


def _build_ai_schema(allowed_folders: List[str], append_candidates: List[str]) -> Dict[str, Any]:
    note_types = [
        "article",
        "ai-summary",
        "prompt",
        "framework",
        "customer",
        "vendor",
        "meeting",
        "decision",
        "reference",
        "playbook",
        "idea",
        "quote",
        "other",
    ]

    schema: Dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "note_type": {"type": "string", "enum": note_types},
            "operation": {"type": "string", "enum": ["create", "append"]},
            "target_folder": {"type": "string", "enum": allowed_folders},
            "target_filename": {
                "type": "string",
                # v1: local code still sanitizes; this nudges the model toward safe names.
                "pattern": r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,200}\.md$",
            },
            "append_target": {
                "type": "string",
                # For append operations, the model must pick an existing file from this list.
                # For create operations, it must return "".
                "enum": [""] + list(append_candidates),
            },
            "title": {"type": "string"},
            "tags": {
                "type": "array",
                "items": {
                    "type": "string",
                    "pattern": r"^[A-Za-z0-9][A-Za-z0-9/_-]{0,63}$",
                },
            },
            "markdown": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": [
            "confidence",
            "note_type",
            "operation",
            "target_folder",
            "target_filename",
            "append_target",
            "title",
            "tags",
            "markdown",
            "rationale",
        ],
    }
    return schema


def _build_ai_prompt(
    *,
    cfg: Config,
    capture_kind: str,
    source_url: str,
    source_title: str,
    source_markdown: str,
    source_text: str,
    candidate_append_targets: List[str],
) -> Tuple[str, str]:
    allowed_folders = cfg.allowed_folders

    instructions = "\n".join(
        [
            "You are Brain Capture. Your job is to route a clipboard capture into a Markdown vault.",
            "",
            "Return ONLY JSON that matches the provided JSON Schema (Structured Outputs).",
            "No markdown fences. No extra keys.",
            "",
            "Rules:",
            f"- target_folder MUST be one of: {', '.join(allowed_folders)}",
            "- target_filename MUST be a safe filename ending in .md (no paths).",
            "- operation is create or append.",
            "- append_target is required: for create it MUST be \"\". For append it MUST be exactly one of the provided APPEND_CANDIDATES paths.",
            "- Use append ONLY if you are highly confident the content belongs in an existing note AND you can pick a correct append_target from APPEND_CANDIDATES.",
            "- For create: markdown should be the note body (NO YAML frontmatter; it will be added locally).",
            "- For append: markdown should be ONLY the content to append (NO frontmatter). Prefer adding a dated heading if appropriate.",
            "- tags must be plain strings WITHOUT '#'. Use lowercase kebab-case when possible.",
            "",
            "If unsure about folder/filename or append target, choose create with a conservative folder and a descriptive filename.",
        ]
    )

    # Keep input smaller and explicit; the model already sees allowed folder enum in schema,
    # but we restate it in plain text.
    append_list = "\n".join(f"- {p}" for p in candidate_append_targets) or "(none)"

    if capture_kind == "url":
        user_input = "\n".join(
            [
                "CAPTURE_KIND: url",
                f"URL: {source_url}",
                f"PAGE_TITLE: {source_title}",
                "",
                "EXTRACTED_MARKDOWN:",
                source_markdown[:60_000],
                "",
                "APPEND_CANDIDATES (vault-relative):",
                append_list,
            ]
        )
    else:
        user_input = "\n".join(
            [
                "CAPTURE_KIND: text",
                "",
                "CLIPBOARD_TEXT:",
                source_text[:60_000],
                "",
                "APPEND_CANDIDATES (vault-relative):",
                append_list,
            ]
        )

    return instructions, user_input


def _call_openai_structured(
    cfg: Config, instructions: str, user_input: str, *, append_candidates: List[str]
) -> Dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment.")

    try:
        import requests  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency requests. Install with: python3 -m pip install -r requirements.txt"
        ) from e

    try:
        from jsonschema import Draft202012Validator  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency jsonschema. Install with: python3 -m pip install -r requirements.txt"
        ) from e

    schema = _build_ai_schema(cfg.allowed_folders, append_candidates)

    payload = {
        "model": cfg.openai_model,
        "instructions": instructions,
        "input": user_input,
        "max_output_tokens": cfg.openai_max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "brain_capture_result",
                "strict": True,
                "schema": schema,
            }
        },
    }

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=cfg.openai_timeout_seconds,
    )
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        raise RuntimeError(f"OpenAI API error: HTTP {r.status_code}: {r.text[:500]}")

    if r.status_code >= 400:
        msg = data.get("error", {}).get("message") if isinstance(data, dict) else None
        raise RuntimeError(f"OpenAI API error: HTTP {r.status_code}: {msg or str(data)[:500]}")

    out_text = _extract_response_output_text(data)
    try:
        out_json = json.loads(out_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Model output was not valid JSON: {e}: {out_text[:500]}")

    # Local validation (defense in depth).
    Draft202012Validator(_build_ai_schema(cfg.allowed_folders, append_candidates)).validate(out_json)
    return out_json


def _extract_response_output_text(resp: Dict[str, Any]) -> str:
    # Some responses include a convenience field.
    ot = resp.get("output_text")
    if isinstance(ot, str) and ot.strip():
        return ot.strip()

    output = resp.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    t = part["text"].strip()
                    if t:
                        return t

    raise RuntimeError("Could not find output_text in OpenAI response.")


def _apply_ai_result(cfg: Config, result: Dict[str, Any], *, source_url: str) -> str:
    confidence = float(result["confidence"])
    op = str(result["operation"])
    folder = str(result["target_folder"])
    filename = str(result["target_filename"])
    append_target = str(result["append_target"])
    title = str(result["title"]).strip() or "Untitled"
    tags = list(result["tags"])
    markdown_body = str(result["markdown"]).rstrip() + "\n"

    # Always sanitize and enforce local rules, regardless of structured output.
    folder = _enforce_folder(cfg, folder, confidence)
    safe_filename = _sanitize_filename(filename or title)

    target_rel = f"{folder}/{safe_filename}"
    target_path = _safe_join_vault(cfg.vault_path, target_rel)

    if confidence < cfg.review_threshold:
        # Safety fallback: always create a new file in _review/ when unsure.
        folder = "_review"
        safe_filename = _sanitize_filename(title)
        target_rel = f"{folder}/{_ensure_md_ext(safe_filename)}"
        target_path = _safe_join_vault(cfg.vault_path, target_rel)
        op = "create"

    if op == "append":
        if not append_target:
            return _write_review_fallback(
                cfg, title, markdown_body, source_url, reason="append operation without append_target"
            )
        target_path = _safe_join_vault(cfg.vault_path, append_target)
        if not target_path.exists():
            return _write_review_fallback(cfg, title, markdown_body, source_url, reason="append target missing")
        if target_path.suffix.lower() != ".md":
            return _write_review_fallback(cfg, title, markdown_body, source_url, reason="append target not .md")
        _append_markdown(target_path, markdown_body)
        changed_paths = [target_path]
    else:
        target_path = _dedupe_create_path(target_path)
        note = _render_frontmatter_note(
            title=title,
            tags=tags,
            note_type=str(result["note_type"]),
            source_url=source_url,
            body_markdown=markdown_body,
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(note, encoding="utf-8")
        changed_paths = [target_path]

    audit_path = _append_audit_log(cfg, result, target_path, source_url=source_url, executed_op=op)
    if audit_path:
        changed_paths.append(audit_path)

    if cfg.git_enabled and cfg.git_auto_commit:
        _git_commit(cfg, changed_paths, title=title)

    rel = target_path.relative_to(cfg.vault_path).as_posix()
    if confidence < cfg.review_threshold:
        return f"Saved to review: {rel}"
    return f"Saved: {rel}"


def _write_review_fallback(cfg: Config, title: str, markdown_body: str, source_url: str, *, reason: str) -> str:
    folder = "_review"
    safe_filename = _sanitize_filename(title)
    rel = f"{folder}/{_ensure_md_ext(safe_filename)}"
    path = _safe_join_vault(cfg.vault_path, rel)
    path = _dedupe_create_path(path)
    note = _render_frontmatter_note(
        title=f"[Review] {title}",
        tags=["needs-review"],
        note_type="other",
        source_url=source_url,
        body_markdown=f"> Fallback reason: {reason}\n\n" + markdown_body,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note, encoding="utf-8")
    _append_audit_log(cfg, {"fallback_reason": reason}, path, source_url=source_url, executed_op="create_review")
    rel2 = path.relative_to(cfg.vault_path).as_posix()
    return f"Saved to review: {rel2}"


def _render_frontmatter_note(
    *,
    title: str,
    tags: List[str],
    note_type: str,
    source_url: str,
    body_markdown: str,
) -> str:
    tags_clean = [t.lstrip("#").strip() for t in tags if isinstance(t, str) and t.strip()]
    tags_clean = [_sanitize_tag(t) for t in tags_clean if _sanitize_tag(t)]

    fm: List[str] = ["---", f'title: "{_escape_yaml_str(title)}"']
    fm.append("tags:")
    if tags_clean:
        for t in tags_clean:
            fm.append(f"  - {t}")
    else:
        fm.append("  - capture")
    fm.append(f"note_type: {note_type}")
    fm.append(f"captured_at: {_now_iso()}")
    if source_url:
        fm.append(f'source: "{_escape_yaml_str(source_url)}"')
    fm.append("---\n")

    return "\n".join(fm) + body_markdown.strip() + "\n"


def _escape_yaml_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _sanitize_tag(t: str) -> str:
    t = t.strip().lower()
    t = t.replace(" ", "-")
    t = re.sub(r"[^a-z0-9/_-]", "", t)
    return t[:64]


def _append_markdown(path: Path, content: str) -> None:
    # Strictly append at end; never insert or modify existing content.
    existing = path.read_text(encoding="utf-8", errors="replace")
    sep = "\n" if existing.endswith("\n") else "\n\n"
    path.write_text(existing + sep + content.lstrip("\n"), encoding="utf-8")


def _append_audit_log(
    cfg: Config, result: Dict[str, Any], target_path: Path, *, source_url: str, executed_op: str
) -> Path | None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    audit_path = cfg.log_dir / "audit.jsonl"
    safe_result: Dict[str, Any] = {}
    # Avoid logging full note contents; keep audit logs metadata-only.
    for k in [
        "confidence",
        "note_type",
        "operation",
        "target_folder",
        "target_filename",
        "append_target",
        "title",
        "tags",
    ]:
        if k in result:
            safe_result[k] = result[k]
    # Allow internal fallback reason for debugging without copying content.
    if "fallback_reason" in result:
        safe_result["fallback_reason"] = result["fallback_reason"]
    rec = {
        "ts": _now_iso(),
        "executed_operation": executed_op,
        "target_path": str(target_path),
        "source_url": source_url,
        "result": safe_result,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return audit_path


def _git_commit(cfg: Config, paths: List[Path], *, title: str) -> None:
    # Only operate inside the vault repo.
    vault = cfg.vault_path
    rel_paths: List[str] = []
    for p in paths:
        try:
            rel_paths.append(str(p.relative_to(vault)))
        except ValueError:
            # If a path isn't inside the vault, skip it (audit log might live elsewhere).
            continue
    if not rel_paths:
        return
    subprocess.run(["git", "-C", str(vault), "add", "--"] + rel_paths, check=False)
    msg = f"Capture: {title}".strip()
    subprocess.run(["git", "-C", str(vault), "commit", "-m", msg], check=False)


def _enforce_folder(cfg: Config, folder: str, confidence: float) -> str:
    # Defense in depth: never allow folders outside the configured allow-list.
    if folder in cfg.allowed_folders:
        return folder
    return "_review"


def _sanitize_filename(name_or_title: str) -> str:
    s = (name_or_title or "").strip()
    if not s:
        s = "capture"
    s = s.replace("/", "-").replace("\\", "-")
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Strip .md then re-add to normalize.
    s = re.sub(r"\.md$", "", s, flags=re.IGNORECASE)

    # Limit to a conservative character set for filenames.
    s = re.sub(r"[^A-Za-z0-9._ -]", "", s).strip(" .-_")
    if not s:
        s = "capture"

    # Keep it short-ish.
    s = s[:180].rstrip(" .-_")
    return _ensure_md_ext(s)


def _ensure_md_ext(s: str) -> str:
    return s if s.lower().endswith(".md") else (s + ".md")


def _dedupe_create_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not find a unique filename to create.")


def _safe_join_vault(vault_path: Path, rel_posix: str) -> Path:
    rel = Path(rel_posix)
    # Prevent path traversal regardless of OS.
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError("Unsafe path traversal attempt blocked.")

    target = (vault_path / rel).resolve()
    vault_resolved = vault_path.resolve()
    if vault_resolved not in target.parents and target != vault_resolved:
        raise RuntimeError("Resolved path escapes the vault_path.")
    return target


def _fetch_url_safely(cfg: Config, url: str) -> Tuple[str, str]:
    try:
        import requests  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency requests. Install with: python3 -m pip install -r requirements.txt"
        ) from e

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("Only http/https URLs are supported.")
    if not parsed.hostname:
        raise RuntimeError("URL missing hostname.")
    _block_private_hosts(parsed.hostname)

    headers = {
        "User-Agent": "BrainCapture/0.1 (+https://example.invalid)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    with requests.get(
        url,
        headers=headers,
        timeout=(5, cfg.url_timeout_seconds),
        stream=True,
        allow_redirects=True,
    ) as r:
        r.raise_for_status()
        ct = (r.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ct and ct not in {"text/html", "application/xhtml+xml"} and not ct.startswith("text/html"):
            raise RuntimeError(f"Unsupported URL content type: {ct}")
        final_url = str(r.url)
        final_parsed = urlparse(final_url)
        if final_parsed.scheme not in ("http", "https") or not final_parsed.hostname:
            raise RuntimeError("Redirected to unsupported URL scheme/host.")
        _block_private_hosts(final_parsed.hostname)

        cl = r.headers.get("Content-Length")
        if cl and cl.isdigit() and int(cl) > cfg.url_max_bytes:
            raise RuntimeError("URL content too large.")

        chunks: List[bytes] = []
        total = 0
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > cfg.url_max_bytes:
                raise RuntimeError("URL content exceeded max_bytes.")
            chunks.append(chunk)
        raw = b"".join(chunks)

        encoding = r.encoding or "utf-8"
        try:
            html = raw.decode(encoding, errors="replace")
        except LookupError:
            html = raw.decode("utf-8", errors="replace")
        return html, final_url


def _block_private_hosts(hostname: str) -> None:
    hn = hostname.strip().lower()
    if hn in {"localhost"} or hn.endswith(".local"):
        raise RuntimeError("Blocked URL host (local).")

    try:
        infos = socket.getaddrinfo(hn, None)
    except socket.gaierror:
        # If DNS fails, requests will fail anyway.
        return

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
        ):
            raise RuntimeError("Blocked URL host (private/reserved IP).")


def _extract_readable_html(html: str) -> Tuple[str, str]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency beautifulsoup4. Install with: python3 -m pip install -r requirements.txt"
        ) from e

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            title = str(og["content"]).strip()

    # Prefer semantic containers.
    candidates = []
    for selector in ["article", "main"]:
        el = soup.find(selector)
        if el:
            candidates.append(el)

    body = soup.body or soup
    candidates.append(body)

    best_html = str(body)
    best_score = 0

    def score(el) -> int:
        text = el.get_text(" ", strip=True)
        return len(text)

    for el in candidates:
        s = score(el)
        if s > best_score:
            best_score = s
            best_html = str(el)

    return title, best_html


def _html_to_markdown(html: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency beautifulsoup4. Install with: python3 -m pip install -r requirements.txt"
        ) from e

    try:
        from markdownify import markdownify as html_to_md  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency markdownify. Install with: python3 -m pip install -r requirements.txt"
        ) from e

    # Strip nav/header/footer-ish tags before markdown conversion to reduce noise.
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["nav", "header", "footer", "aside", "form", "button", "input"]):
        tag.decompose()
    cleaned = str(soup)
    md = html_to_md(cleaned, heading_style="ATX", bullets="*")
    md = md.strip()
    return md + "\n"


def _open_path(p: Path) -> None:
    if os.environ.get("BRAIN_CAPTURE_NO_OPEN") == "1":
        return
    subprocess.run(["open", str(p)], check=False)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")
