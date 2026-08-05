"""Validate vLLM configuration without printing credentials."""

from __future__ import annotations

import argparse
import os
import shutil


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    required = ["VLLM_MODEL_PATH", "VLLM_API_KEY"]
    presence = {name: bool(os.getenv(name)) for name in required}
    binaries = {name: shutil.which(name) is not None for name in ["vllm", "nvidia-smi"]}
    result = {"env_present": presence, "binaries_present": binaries, "dry_run": args.dry_run}
    print(result)
    if args.dry_run:
        return
    missing = [name for name, present in presence.items() if not present]
    if missing:
        raise SystemExit(f"MISSING_PRIVATE_CONFIG:{','.join(missing)}")


if __name__ == "__main__":
    main()
