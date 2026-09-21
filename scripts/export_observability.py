"""Export project-local evidence with provenance; no application/account log scan.

Usage: python scripts/export_observability.py --source label=directory [...]
       --output evidence/run --run-id run-name
Source trees are snapshotted, not changed. Review output before publication.
"""
import argparse
import hashlib
import gzip
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SENSITIVE_KEY = re.compile(r"token|password|secret|api.?key|authorization|credential|email|device.?id|user.?id|steam.?id|account.?id", re.I)
PRIVATE_FILE = re.compile(r"token|password|secret|credential|auth|localconfig|cloud.storage", re.I)
EXCLUDED_SUFFIXES = (".vtube.json", ".motion3.json")
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".log"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    sources = []
    for spec in args.source:
        label, directory = spec.split("=", 1)
        if not re.fullmatch(r"[a-z0-9-]+", label):
            raise ValueError("Source labels must be portable lowercase identifiers")
        sources.append((label, Path(directory).resolve(strict=True)))
    if len({label for label, _ in sources}) != len(sources):
        raise ValueError("Duplicate source labels")
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("Use a new output directory to preserve snapshots")
    output.mkdir(parents=True)
    exported_at = datetime.now(timezone.utc).isoformat()
    redactions = Counter()

    def clean_text(text):
        if not any(marker in text for marker in (":", "\\", "/", "@", "=", "gh", "sk-")):
            return text
        # Longest root first keeps cross-file references stable and readable.
        for label, root in sorted(sources, key=lambda pair: len(str(pair[1])), reverse=True):
            for spelling in {str(root), root.as_posix()}:
                text, count = re.subn(re.escape(spelling), lambda _: "${" + label + "}", text, flags=re.I)
                redactions["source_root"] += count
        for pattern, replacement, reason in [
            (r"[A-Za-z]:[\\/][^\r\n\t\"<>|]*", "[LOCAL_PATH]", "absolute_path"),
            (r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", "email"),
            (r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{20,})\b", "[SECRET]", "secret_pattern"),
            (r"(?im)\b(?:api[_ -]?key|auth(?:entication)?[_ -]?token|password|secret)\s*[:=]\s*[^\s,;]+", "[REDACTED_CREDENTIAL_ASSIGNMENT]", "credential_assignment"),
        ]:
            text, count = re.subn(pattern, replacement, text)
            redactions[reason] += count
        return text

    def sanitize(value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if SENSITIVE_KEY.search(key):
                    result[key] = "[REDACTED_FIELD]"
                    redactions["sensitive_field"] += 1
                else:
                    result[clean_text(key)] = sanitize(item)
            return result
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return clean_text(value) if isinstance(value, str) else value

    records, inventory = [], []
    for label, root in sources:
        # Scratch sources are top-level only: avoid other clones and media staging.
        paths = root.iterdir() if label in {"scratch", "native-stage"} else root.rglob("*")
        for path in sorted(paths):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            entry = {"source": label, "path": relative, "bytes": path.stat().st_size,
                     "source_mtime_utc": utc(path.stat().st_mtime)}
            reason = None
            if PRIVATE_FILE.search(path.name):
                reason = "potential_credentials"
            elif path.name.endswith(EXCLUDED_SUFFIXES):
                reason = "private_runtime_configuration_or_tracking_curves"
            elif path.suffix.lower() not in TEXT_SUFFIXES:
                reason = "binary_asset_or_source_code_not_log"
            elif entry["bytes"] > 64 * 1024 * 1024:
                reason = "text_exceeds_64MiB_review_limit"
            if reason:
                entry.update(disposition="metadata_only", reason=reason)
                inventory.append(entry)
                continue
            raw = path.read_bytes()
            entry["source_sha256"] = digest(raw)
            try:
                text = raw.decode("utf-8-sig")
                if path.suffix == ".json":
                    payload = json.loads(text)
                    kind = "json"
                elif path.suffix == ".jsonl":
                    payload = [json.loads(line) for line in text.splitlines() if line.strip()]
                    kind = "jsonl"
                else:
                    payload, kind = text, "text"
            except (UnicodeError, ValueError) as error:
                entry.update(disposition="metadata_only", reason=type(error).__name__)
                inventory.append(entry)
                continue
            before = redactions.copy()
            payload = sanitize(payload)
            transformed = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            record_id = digest((label + "/" + relative).encode())[:20]
            record = {"schema_version": 1, "record_id": record_id, "run_id": args.run_id,
                      "source": label, "path": relative, "source_sha256": entry["source_sha256"],
                      "source_bytes": len(raw), "source_mtime_utc": entry["source_mtime_utc"],
                      "exported_at_utc": exported_at, "payload_kind": kind,
                      "payload_sha256": digest(transformed),
                      "redactions": dict(redactions - before),
                      "evidence_semantics": "historical_source_record_not_current_acceptance",
                      "payload": payload}
            records.append(record)
            entry.update(disposition="included", record_id=record_id)
            inventory.append(entry)
    def write_lines(name, values):
        (output / name).write_text("".join(json.dumps(v, ensure_ascii=False, sort_keys=True) + "\n" for v in values), encoding="utf-8")
    with (output / "records.jsonl.gz").open("wb") as target:
        with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as compressed:
            for record in records:
                compressed.write((json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode())
    write_lines("inventory.jsonl", inventory)
    manifest = {"schema_version": 1, "run_id": args.run_id, "exported_at_utc": exported_at,
                "source_labels": [label for label, _ in sources],
                "records": len(records), "inventory_entries": len(inventory),
                "dispositions": dict(Counter(e["disposition"] for e in inventory)),
                "exclusion_reasons": dict(Counter(e.get("reason") for e in inventory if "reason" in e)),
                "redactions": dict(redactions),
                "limitations": ["mtime is not event time", "historical statuses may be superseded",
                                "no system, account or complete chat logs collected",
                                "binary assets, runtime user configuration and tracking curves are metadata only",
                                "source hash identifies private original; payload hash identifies sanitized canonical JSON",
                                "sanitization can remove useful context; review redaction counts"],
                "files": {name: digest((output / name).read_bytes()) for name in ("records.jsonl.gz", "inventory.jsonl")}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
