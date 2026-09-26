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

# upload_folder не читает .gitignore и выложил бы ВСЁ из папки: ключи
# (config/api_keys.json), сессии Telegram, базу памяти, картинки — Space
# отклоняет бинарные файлы, а ключи в публичном Space — утечка.
IGNORE = [
    ".git/*", "**/.git/*", "config/api_keys.json", "**/api_keys.json", "*.session", "**/*.session",
    "*.session-journal", "**/*.session-journal", "*.env", ".env", "**/.env",
    "memory/*", "*.db", "**/*.db", "*.log", "**/*.log", "browser/*", "models/*",
    "*.zip", "*.ico", "*.png", "*.jpg", "*.exe", "assets/*", "__pycache__/*", "**/__pycache__/*",
    ".venv/*", "venv/*",
]

api = HfApi(token=TOKEN)
commit_info = api.upload_folder(
    folder_path=folder_path,
    repo_id=repo_id,
    repo_type="space",
    ignore_patterns=IGNORE,
    commit_message="deploy",
)
print("Deployed:", commit_info)
