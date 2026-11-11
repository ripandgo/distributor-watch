"""State kept in the repo's state/ dir, one JSON file per source.
Items are written one per line (sorted) so git stores small deltas."""
import json, os

STATE_DIR = os.environ.get("WATCH_STATE_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")


# Not committed to the (public) repo: ASM dealer prices and Gmail message ids.
# Kept in state/private/, which the workflow persists with actions/cache.
PRIVATE = {"asm_latest", "gmail_seen"}


def path(name):
    d = os.path.join(STATE_DIR, "private") if name in PRIVATE else STATE_DIR
    return os.path.join(d, f"{name}.json")


def load(name, default=None):
    p = path(name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    p = path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if isinstance(obj, dict) and isinstance(obj.get("items"), dict):
            # line-per-item layout for compact git diffs
            meta = {k: v for k, v in obj.items() if k != "items"}
            f.write("{\n")
            for k in sorted(meta):
                f.write(f"{json.dumps(k)}: {json.dumps(meta[k], ensure_ascii=False, sort_keys=True)},\n")
            f.write('"items": {\n')
            keys = sorted(obj["items"])
            for i, k in enumerate(keys):
                sep = "," if i < len(keys) - 1 else ""
                f.write(f"{json.dumps(k, ensure_ascii=False)}: "
                        f"{json.dumps(obj['items'][k], ensure_ascii=False, sort_keys=True)}{sep}\n")
            f.write("}\n}\n")
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
    os.replace(tmp, p)
