#!/usr/bin/env bash
# Instala a skill por link simbólico. Uso: bash install.sh [claude|codex|all]  (padrão: all)
set -euo pipefail

SKILL_NAME="koko-seo-geo-llmo-aeo"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALVO="${1:-all}"

instala() {
  local skills_dir="$1" app="$2"
  local target_link="$skills_dir/$SKILL_NAME"
  mkdir -p "$skills_dir"
  if [ -L "$target_link" ]; then
    rm "$target_link"
  elif [ -e "$target_link" ]; then
    echo "Já existe algo em $target_link que não é link. Mova ou apague e rode de novo."
    return 1
  fi
  ln -s "$REPO_DIR" "$target_link"
  echo "Instalada no $app: $target_link (reinicie o $app para carregar)"
}

case "$ALVO" in
  claude) instala "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" "Claude Code" ;;
  codex)  instala "${CODEX_HOME:-$HOME/.codex}/skills" "Codex" ;;
  all)
    instala "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" "Claude Code"
    instala "${CODEX_HOME:-$HOME/.codex}/skills" "Codex"
    ;;
  *) echo "Uso: bash install.sh [claude|codex|all]"; exit 1 ;;
esac
