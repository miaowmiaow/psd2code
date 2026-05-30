#!/usr/bin/env bash
# 把 ~/psd2code（内网）的业务代码同步到 ~/psd2code-github（GitHub），并推送到 GitHub
# 用法: bash scripts/sync-to-github.sh [commit-message]

set -euo pipefail

WOA_DIR="$HOME/psd2code"
GITHUB_DIR="$HOME/psd2code-github"

if [ ! -d "$WOA_DIR/.git" ]; then
  echo "❌ 未找到内网仓库: $WOA_DIR"
  exit 1
fi
if [ ! -d "$GITHUB_DIR/.git" ]; then
  echo "❌ 未找到 GitHub 仓库: $GITHUB_DIR"
  exit 1
fi

echo "🔄 同步业务文件: $WOA_DIR → $GITHUB_DIR"

# rsync 同步业务文件
# --delete: 删除 GitHub 目录里内网已不存在的文件（保证一致）
# --exclude: 保护 GitHub 专属文件 + 忽略本地构建产物
rsync -av --delete \
  --exclude='.git/' \
  --exclude='LICENSE' \
  --exclude='.github/' \
  --exclude='CONTRIBUTING.md' \
  --exclude='README.en.md' \
  --exclude='pyproject.toml' \
  --exclude='output/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='.venv/' \
  --exclude='venv/' \
  --exclude='build/' \
  --exclude='dist/' \
  --exclude='*.egg-info/' \
  --exclude='.DS_Store' \
  --exclude='.idea/' \
  --exclude='.vscode/' \
  "$WOA_DIR/" "$GITHUB_DIR/"

# 提交并推送
cd "$GITHUB_DIR"

if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
  echo "ℹ️  无变更，无需推送 GitHub"
  exit 0
fi

git add -A
MSG="${1:-sync from internal: $(date '+%Y-%m-%d %H:%M:%S')}"
git commit -m "$MSG"
git push origin master
echo "✅ 已同步并推送到 GitHub"
