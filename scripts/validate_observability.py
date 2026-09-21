"""Validate archive integrity and print a compact, queryable record index.

python scripts/validate_observability.py evidence/run [--index work/index.jsonl]
"""
import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--index", type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.archive / "manifest.json").read_text(encoding="utf-8"))
    for filename, expected in manifest["files"].items():
        actual = hashlib.sha256((args.archive / filename).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"File hash mismatch: {filename}")
    inventory = [json.loads(line) for line in (args.archive / "inventory.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(inventory) != manifest["inventory_entries"]:
        raise ValueError("Inventory count mismatch")
    included = {entry["record_id"]: entry for entry in inventory if entry["disposition"] == "included"}
    seen, index = set(), []
    with gzip.open(args.archive / "records.jsonl.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            rid = record["record_id"]
            if rid in seen or rid not in included:
                raise ValueError("Duplicate or unlisted record")
            seen.add(rid)
            if record["schema_version"] != 1 or record["run_id"] != manifest["run_id"]:
                raise ValueError("Record schema/run mismatch")
            for key in ("source", "path", "source_sha256", "source_mtime_utc"):
                if record[key] != included[rid][key]:
                    raise ValueError(f"Inventory mismatch: {rid} {key}")
            canonical = json.dumps(record["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            if hashlib.sha256(canonical).hexdigest() != record["payload_sha256"]:
                raise ValueError(f"Payload hash mismatch: {rid}")
            if re.search(r"[A-Za-z]:[\\/]|\bgh[pousr]_[A-Za-z0-9_]{20,}|\bsk-[A-Za-z0-9_-]{20,}", line):
                raise ValueError(f"Potential private data requires review: {rid}")
            item = {key: record[key] for key in ("record_id", "source", "path", "payload_kind", "source_mtime_utc", "redactions")}
            if isinstance(record["payload"], dict):
                item["payload_keys"] = list(record["payload"].keys())
                item["reported_fields"] = {key: value for key, value in record["payload"].items()
                                           if key in {"status", "stage", "at", "result", "sampleCount", "runtimeVerified", "videoVerified", "qualityAccepted", "fullModelAccepted"}
                                           and not isinstance(value, (dict, list))}
            index.append(item)
    if seen != set(included) or len(seen) != manifest["records"]:
        raise ValueError("Record coverage mismatch")
    if args.index:
        args.index.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in index), encoding="utf-8", newline="\n")
    print(json.dumps({"integrity": "pass", "records": len(seen), "inventory_entries": len(inventory),
                      "privacy_pattern_scan": "pass_not_a_complete_privacy_guarantee"}))


if __name__ == "__main__":
    main()
