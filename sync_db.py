"""Download the CI database before running the pipeline locally.

Usage: python sync_db.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

DB_PATH = os.path.join("data", "clips.db")
REPO = "jahruggdd/twitch-to-shorts"
WORKFLOW = "pipeline.yml"
ARTIFACT_NAME = "clips-db"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def main():
    # Find latest successful pipeline run
    result = run([
        "gh", "run", "list",
        "--repo", REPO,
        "--workflow", WORKFLOW,
        "--status", "success",
        "--limit", "1",
        "--json", "databaseId",
    ])
    if result.returncode != 0:
        print(f"Failed to list runs: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    import json
    runs = json.loads(result.stdout)
    if not runs:
        print("No successful pipeline runs found", file=sys.stderr)
        sys.exit(1)

    run_id = str(runs[0]["databaseId"])
    print(f"Downloading DB from CI run {run_id}...")

    # Download artifact to temp dir
    with tempfile.TemporaryDirectory() as tmp:
        result = run([
            "gh", "run", "download", run_id,
            "--repo", REPO,
            "--name", ARTIFACT_NAME,
            "--dir", tmp,
        ])
        if result.returncode != 0:
            print(f"Failed to download artifact: {result.stderr}", file=sys.stderr)
            print("Has the CI pipeline run since the artifact step was added?", file=sys.stderr)
            sys.exit(1)

        src = os.path.join(tmp, "clips.db")
        if not os.path.exists(src):
            print(f"Downloaded artifact missing clips.db", file=sys.stderr)
            sys.exit(1)

        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        shutil.copy2(src, DB_PATH)
        size = os.path.getsize(DB_PATH)
        print(f"Synced {DB_PATH} ({size:,} bytes) from CI run {run_id}")


if __name__ == "__main__":
    main()
