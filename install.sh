#!/usr/bin/env bash
# nfseai installer: instala a skill nos seus agentes de IA e o comando `nfseai`.
#   curl -fsSL https://raw.githubusercontent.com/pycodebr/nfseai/main/install.sh | bash
#   bash install.sh [--agent claude|codex|opencode|gemini|agy|cursor|hermes|all] [--copy] [--pdf] [--no-setup]
#   bash install.sh --uninstall        # remove a skill dos agentes; mantém ~/.nfseai
set -euo pipefail
REPO="https://github.com/pycodebr/nfseai.git"
HOME_DIR="${NFSEAI_HOME:-$HOME/.nfseai}"
SRC="${NFSEAI_SRC:-$HOME_DIR/src}"
AGENT="auto"; MODE="link"; RUN_SETUP=1; UNINSTALL=0; SETUP_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) AGENT="${2:-}"; shift 2 ;;
    --copy) MODE="copy"; shift ;;
    --no-setup) RUN_SETUP=0; shift ;;
    --pdf) SETUP_ARGS+=(--pdf); shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,5p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || true; exit 0 ;;
    *) echo "opção desconhecida: $1" >&2; exit 2 ;;
  esac
done
say() { printf '\033[1;32m[nfseai]\033[0m %s\n' "$*"; }

# funções com case em vez de arrays associativos: o macOS traz o bash 3.2
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
HERMES_DIR="${HERMES_HOME:-$HOME/.hermes}"
OPENCODE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
skill_dir_for() {
  case "$1" in
    claude) echo "$CLAUDE_DIR/skills" ;;
    codex|gemini|agents) echo "$HOME/.agents/skills" ;;
    opencode) echo "$OPENCODE_DIR/skills" ;;
    agy) echo "$HOME/.gemini/config/skills" ;;
    cursor) echo "$HOME/.cursor/skills" ;;
    hermes) echo "$HERMES_DIR/skills/productivity" ;;
    *) echo "" ;;
  esac
}
marker_for() {
  case "$1" in
    claude) echo "$CLAUDE_DIR" ;;
    codex) echo "$CODEX_DIR" ;;
    gemini) echo "$HOME/.gemini" ;;
    opencode) echo "$OPENCODE_DIR" ;;
    agy) echo "$HOME/.gemini/antigravity-cli" ;;
    cursor) echo "$HOME/.cursor" ;;
    hermes) echo "$HERMES_DIR" ;;
  esac
}
ALL_AGENTS="claude codex gemini opencode agy cursor hermes"
targets=()
case "$AGENT" in
  all) for a in $ALL_AGENTS; do targets+=("$a"); done ;;
  auto) for a in $ALL_AGENTS; do [[ -d "$(marker_for "$a")" ]] && targets+=("$a"); done
        [[ ${#targets[@]} -gt 0 ]] || targets=(agents) ;;
  *) targets=("$AGENT") ;;
esac

if [[ $UNINSTALL -eq 1 ]]; then
  for a in $ALL_AGENTS; do
    d="$(skill_dir_for "$a")"
    if [[ -L "$d/nfseai" || -d "$d/nfseai" ]]; then rm -rf "$d/nfseai"; say "skill removida de $d"; fi
  done
  rm -f "$HOME/.local/bin/nfseai"
  say "pronto. Suas credenciais, padrões fiscais e o registro continuam em $HOME_DIR; apague a pasta se quiser removê-los."
  exit 0
fi

# o código: este checkout, ou um clone atualizado em ~/.nfseai/src
HERE=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; fi
if [[ -n "$HERE" && -f "$HERE/skills/nfseai/SKILL.md" ]]; then SRC="$HERE"
elif [[ -d "$SRC/.git" ]]; then git -C "$SRC" pull --ff-only -q
else
  command -v git >/dev/null || { say "o git é necessário. Instale o git e rode de novo."; exit 1; }
  mkdir -p "$(dirname "$SRC")"; chmod 700 "$HOME_DIR" 2>/dev/null || true
  git clone -q --depth 1 "$REPO" "$SRC"
fi
SKILL="$SRC/skills/nfseai"
say "código: $SRC"

done_dirs=""
for a in "${targets[@]}"; do
  d="$(skill_dir_for "$a")"
  [[ -n "$d" ]] || { say "agente desconhecido: $a (opções: $ALL_AGENTS all)"; exit 2; }
  case " $done_dirs " in *" $d "*) say "skill disponível para $a em $d/nfseai"; continue ;; esac
  done_dirs="$done_dirs $d"
  mkdir -p "$d"; rm -rf "$d/nfseai"
  if [[ "$MODE" == "copy" ]]; then cp -R "$SKILL" "$d/nfseai"; else ln -s "$SKILL" "$d/nfseai"; fi
  say "skill instalada para $a em $d/nfseai"
done
say "outros agentes: npx skills add pycodebr/nfseai -g"

if [[ $RUN_SETUP -eq 1 ]]; then bash "$SKILL/scripts/setup.sh" ${SETUP_ARGS[@]+"${SETUP_ARGS[@]}"}; fi
echo
say "Falta um passo, que só você pode fazer: conectar a sua conta do portal."
say "  1. No seu terminal, rode:  nfseai conectar   (informe o CNPJ e a senha do Emissor Nacional)"
say "  2. Reinicie o seu agente de IA e peça, por exemplo:"
say "     \"Emita uma nota fiscal de serviço de R\$ 500,00 para o CNPJ 00.000.000/0001-00\""
