"""Review exact staged Phase 9 evidence for public export, beyond credential scanning."""

import hashlib
import json
import re
import subprocess


def git(*args):
    return subprocess.check_output(["git", *args])  # noqa: S603,S607 -- fixed local git command


def main():
    names = git("diff", "--cached", "--name-only").decode().splitlines()
    evidence = [n for n in names if n.startswith("azure_databricks/evidence/phase_09/")
                and n.endswith(".json")]
    keys, findings, strings, digests = set(), [], [], {}

    def walk(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                keys.add(key)
                walk(child, path + "." + key)
        elif isinstance(value, list):
            for child in value:
                walk(child, path + "[]")
        elif isinstance(value, str):
            strings.append(value)
            if re.search(r"[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}|https?://|Bearer |"
                         r"dapi[a-zA-Z0-9]{20,}|eyJ[a-zA-Z0-9_-]{20,}", value):
                findings.append(path)

    for name in evidence:
        data = git("show", ":" + name)
        digests[name] = hashlib.sha256(data).hexdigest()
        walk(json.loads(data), name)
    result = {"staged_file_count": len(names), "evidence_file_count": len(evidence),
              "all_evidence_keys": sorted(keys), "string_value_count": len(strings),
              "sensitive_pattern_paths": findings,
              "longest_values_for_manual_review": sorted(set(strings), key=len, reverse=True)[:8],
              "staged_evidence_sha256": digests,
              "excluded_user_phase5_change": not any("phase_05" in n for n in names)}
    print(json.dumps(result, indent=2))
    assert not findings, "Review flagged evidence before public export"
    assert result["excluded_user_phase5_change"]


if __name__ == "__main__":
    main()
