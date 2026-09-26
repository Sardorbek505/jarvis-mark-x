"""Deploy the exported project tree to the HF Space via the HTTP API (no raw git protocol).

Usage (token comes from env var, never hardcode it here):
    set HF_TOKEN=hf_xxx
    .venv\\Scripts\\python.exe scripts\\deploy_hf.py <path_to_exported_tree>
"""
import os
import sys

from huggingface_hub import HfApi

TOKEN = os.environ.get("HF_TOKEN")
if not TOKEN:
    print("HF_TOKEN env var is not set.", file=sys.stderr)
    sys.exit(1)

if len(sys.argv) != 2:
    print("Usage: deploy_hf.py <path_to_exported_tree>", file=sys.stderr)
    sys.exit(1)

folder_path = sys.argv[1]
repo_id = "atabekovch/jarvis-mark-x"

api = HfApi(token=TOKEN)
commit_info = api.upload_folder(
    folder_path=folder_path,
    repo_id=repo_id,
    repo_type="space",
    commit_message="Sync latest master: живой разговор, self-test, browser panel, video player, Spotify Premium",
)
print("Deployed:", commit_info)
