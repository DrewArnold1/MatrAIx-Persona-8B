#!/usr/bin/env python3
"""Download the public Persona 1M coreset and verify the loaders can see it.

The in-repo dev sample is smoke-only (200 personas, ~56 of them North American
adults). Anything that needs population coverage - a US-adult cohort, a
calibrated draw, subgroup breakdowns - needs this release instead.

    python persona/scripts/fetch_persona_1m.py

The script preflights network reachability first, because the common failure is
not a broken download but an egress policy that denies huggingface.co outright.
When that happens it names the hosts to allow rather than retrying, then exits.

After a successful download it checks the layout against what
backend.service.persona_1m_pool.resolve_1m_paths actually looks for, so a
half-finished or misplaced download is caught here rather than surfacing later
as an empty cohort.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sys
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[2]
HF_REPO = "MatrAIx2026/MatrAIx_Persona_1M_Public_Release"
DEFAULT_DEST = REPO_ROOT / "persona/datasets/matraix-persona-1m/release"

# huggingface_hub resolves metadata on the first host and pulls file content
# from the CDN/storage hosts. An allowlist with only huggingface.co gets you a
# working API call and a failed download, so all of these have to be allowed.
REQUIRED_HOSTS = (
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cdn-lfs-us-1.hf.co",
    "transfer.xethub.hf.co",
    "cas-server.xethub.hf.co",
    "cas-bridge.xethub.hf.co",
)
PROBE_URL = f"https://huggingface.co/api/datasets/{HF_REPO}"


def preflight() -> str | None:
    """Return None if reachable, else a human-readable reason."""
    try:
        with urllib.request.urlopen(PROBE_URL, timeout=30) as response:
            if response.status == 200:
                return None
            return f"unexpected HTTP {response.status} from {PROBE_URL}"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return (
                f"HTTP {exc.code} from huggingface.co. If this is a gated dataset, "
                "log in with `hf auth login`; if it is an egress policy, see below."
            )
        return f"HTTP {exc.code} from huggingface.co"
    except urllib.error.URLError as exc:
        return f"cannot reach huggingface.co: {exc.reason}"
    except (TimeoutError, socket.timeout):
        return "timed out reaching huggingface.co"


def blocked_message(reason: str) -> str:
    hosts = "\n".join(f"      {host}" for host in REQUIRED_HOSTS)
    return f"""
Hugging Face is not reachable from this session.

  {reason}

If you are in a Claude Code cloud session, this is the environment's egress
policy, not a broken download, and it cannot be worked around from inside the
session. Fix it on the environment:

  1. Open claude.ai/code and select the cloud icon above the message box.
  2. Hover the environment, select the settings icon.
  3. Set Network access to Custom, tick "Also include default list of common
     package managers", and add to Allowed domains:

{hosts}

     ("*.hf.co" and "*.huggingface.co" cover the CDN hosts in one line.)

  4. Start a new session - the policy is applied at session start.

Setting Network access to Full also works and needs no host list.

Alternatively, run this script on your own machine and commit or copy the
release to {DEFAULT_DEST.relative_to(REPO_ROOT)}/.
"""


def verify(dest: Path) -> tuple[bool, str]:
    """Check the layout the pool loader actually probes for."""
    if not dest.is_dir():
        return False, f"{dest} does not exist"
    data_dir = dest / "data" if (dest / "data").is_dir() else dest
    parquet = sorted(data_dir.glob("persona-1m-*.parquet"))
    if not parquet:
        return False, (
            f"no persona-1m-*.parquet under {data_dir}. The loader globs exactly "
            "that pattern, so a download that landed elsewhere will not be found."
        )
    schema = dest / "persona_codes.schema.json"
    if not schema.is_file():
        schema = data_dir / "persona_codes.schema.json"
    if not schema.is_file():
        return False, "persona_codes.schema.json is missing from the release root"
    total = sum(path.stat().st_size for path in parquet)
    return True, (
        f"{len(parquet)} parquet file(s), {total / 1e9:.2f} GB, schema present at "
        f"{schema.relative_to(dest)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--repo", default=HF_REPO)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only report reachability and whether a usable release is already on disk.",
    )
    args = parser.parse_args()

    ok, detail = verify(args.dest)
    if ok:
        print(f"Persona 1M already present at {args.dest}")
        print(f"  {detail}")
        print("\nNext:")
        print(
            "  python persona/curation/existing_data/united_states/rake_us_adults.py \\\n"
            "    --pool persona/datasets/matraix-persona-1m --mode sample --sample-size 400"
        )
        return 0

    reason = preflight()
    if reason is not None:
        print(blocked_message(reason), file=sys.stderr)
        return 2
    if args.check_only:
        print("huggingface.co is reachable; no release on disk yet.")
        print(f"  {detail}")
        return 0

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print(
            "huggingface_hub is not installed. Run: uv pip install huggingface_hub",
            file=sys.stderr,
        )
        return 1

    print(f"Downloading {args.repo} -> {args.dest}")
    args.dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=args.repo,
        repo_type="dataset",
        local_dir=str(args.dest),
    )

    ok, detail = verify(args.dest)
    if not ok:
        print(f"Download finished but the layout is wrong:\n  {detail}", file=sys.stderr)
        return 1
    print(f"OK: {detail}")
    print("\nNext:")
    print(
        "  python persona/curation/existing_data/united_states/rake_us_adults.py \\\n"
        "    --pool persona/datasets/matraix-persona-1m --mode sample --sample-size 400"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
