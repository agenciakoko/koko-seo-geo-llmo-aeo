#!/usr/bin/env bash
# Remove o link da skill. Uso: bash uninstall.sh [claude|codex|all]  (padrão: all)
set -euo pipefail

SKILL_NAME="koko-seo-geo-llmo-aeo"
ALVO="${1:-all}"

remove() {
  local target_link="$1/$SKILL_NAME"
  if [ -L "$target_link" ]; then
    rm "$target_link"
    echo "Removido: $target_link"
  elif [ -e "$target_link" ]; then
    echo "Em $target_link há algo que não é link. Não removi."
  else
    echo "Nada para remover em $target_link"
  fi
}

case "$ALVO" in
  claude) remove "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" ;;
  codex)  remove "${CODEX_HOME:-$HOME/.codex}/skills" ;;
  all)    remove "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"; remove "${CODEX_HOME:-$HOME/.codex}/skills" ;;
  *) echo "Uso: bash uninstall.sh [claude|codex|all]"; exit 1 ;;
esac
