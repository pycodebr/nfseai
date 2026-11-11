#!/usr/bin/env bash
# nfseai setup: cria o ambiente Python isolado e instala o comando `nfseai`.
#   bash setup.sh [--pdf] [--quiet]
# Pode ser rodado de novo a qualquer momento: atualiza o pacote e preserva as
# credenciais, os padrões fiscais e o registro em ~/.nfseai.
set -euo pipefail

REPO_URL="https://github.com/pycodebr/nfseai"
HOME_DIR="${NFSEAI_HOME:-$HOME/.nfseai}"
VENV="$HOME_DIR/venv"
BIN_DIR="$HOME_DIR/bin"
WITH_PDF=0; QUIET=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pdf) WITH_PDF=1; shift ;;
    --quiet) QUIET=1; shift ;;
    *) echo "opção desconhecida: $1" >&2; exit 2 ;;
  esac
done
say() { printf '\033[1;32m[nfseai]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[nfseai]\033[0m %s\n' "$*" >&2; exit 1; }

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
REPO_DIR="$(cd "$SKILL_DIR/../.." 2>/dev/null && pwd -P || true)"
VERSION="$(sed -n 's/^  version: *//p' "$SKILL_DIR/SKILL.md" | head -1 | tr -d "\"' ")"

mkdir -p "$HOME_DIR" "$BIN_DIR"
chmod 700 "$HOME_DIR"

# 1. Um Python >= 3.10 ou o uv (que baixa o Python sozinho).
python_ok() { "$1" -c 'import sys, venv, ensurepip; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
PYTHON=""
for candidate in "${NFSEAI_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3 python; do
  [[ -n "$candidate" ]] || continue
  if command -v "$candidate" >/dev/null 2>&1 && python_ok "$candidate"; then PYTHON="$(command -v "$candidate")"; break; fi
done
UV=""
if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"; elif [[ -x "$BIN_DIR/uv" ]]; then UV="$BIN_DIR/uv"; fi
if [[ -z "$PYTHON" && -z "$UV" ]]; then
  command -v curl >/dev/null 2>&1 || fail "é preciso ter Python 3.10+ (com venv) ou curl para baixar o uv. Instale um dos dois e rode de novo."
  say "Python 3.10+ não encontrado; baixando o uv (gerenciador de Python da Astral) para $BIN_DIR"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$BIN_DIR" sh >/dev/null 2>&1 \
    || fail "não foi possível baixar o uv. Instale Python 3.10+ (ex.: sudo apt install python3 python3-venv, ou brew install python) e rode de novo."
  UV="$BIN_DIR/uv"
fi

# 2. Ambiente virtual próprio, para não tocar no Python do sistema.
if [[ ! -x "$VENV/bin/python" ]]; then
  if [[ -n "$PYTHON" ]]; then
    say "criando o ambiente em $VENV ($("$PYTHON" --version 2>&1))"
    "$PYTHON" -m venv "$VENV"
  else
    say "criando o ambiente em $VENV (Python 3.12 pelo uv)"
    "$UV" venv --quiet --python 3.12 "$VENV"
  fi
fi
pip_install() {
  if "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check "$@"
  else
    "$UV" pip install --quiet --python "$VENV/bin/python" "$@"
  fi
}

# 3. O pacote: do checkout quando a skill está dentro dele, senão do GitHub na versão da skill.
if [[ -n "$REPO_DIR" && -f "$REPO_DIR/pyproject.toml" ]] && grep -q '^name = "nfseai"' "$REPO_DIR/pyproject.toml"; then
  if [[ "$REPO_DIR" == "$(cd "$HOME_DIR" && pwd -P)/src" ]]; then
    say "instalando o nfseai $VERSION a partir de $REPO_DIR (atualiza junto com o git pull)"
    pip_install --editable "$REPO_DIR"
  else
    say "instalando o nfseai $VERSION a partir de $REPO_DIR"
    pip_install --upgrade "$REPO_DIR"
  fi
else
  command -v git >/dev/null 2>&1 || fail "o git é necessário para baixar o pacote. Instale o git e rode de novo."
  say "instalando o nfseai $VERSION a partir de $REPO_URL"
  pip_install --upgrade "nfseai @ git+$REPO_URL@v$VERSION" 2>/dev/null \
    || pip_install --upgrade "nfseai @ git+$REPO_URL@main"
fi

# 4. O comando `nfseai`: um lançador estável em ~/.nfseai/bin e um link em ~/.local/bin.
cat > "$BIN_DIR/nfseai" <<LAUNCHER
#!/usr/bin/env bash
exec "$VENV/bin/nfseai" "\$@"
LAUNCHER
chmod 755 "$BIN_DIR/nfseai"
mkdir -p "$HOME/.local/bin"
ln -sf "$BIN_DIR/nfseai" "$HOME/.local/bin/nfseai"

if [[ $WITH_PDF -eq 1 ]]; then
  say "instalando o suporte a PDF (Playwright + Chromium)"
  "$BIN_DIR/nfseai" instalar-pdf >/dev/null || say "o suporte a PDF não foi instalado; tente depois com: nfseai instalar-pdf"
fi

# 5. Conferência.
"$BIN_DIR/nfseai" --version >/dev/null || fail "o comando nfseai não respondeu depois da instalação."
if [[ $QUIET -eq 0 ]]; then
  if DOCTOR="$("$BIN_DIR/nfseai" doctor 2>&1)"; then
    say "conferência ok: Python, dependências, permissões e acesso ao portal www.nfse.gov.br"
  else
    printf '%s\n' "$DOCTOR"
    say "o doctor apontou as pendências acima; o comando foi instalado mesmo assim."
  fi
fi
say "nfseai $("$BIN_DIR/nfseai" --version | awk '{print $2}') instalado: $BIN_DIR/nfseai"
case ":$PATH:" in
  *":$HOME/.local/bin:"*|*":$BIN_DIR:"*) ;;
  *) say "o comando ainda não está no PATH. Use o caminho completo ($BIN_DIR/nfseai) ou adicione ao seu shell: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
say "próximo passo, no seu terminal: nfseai conectar"
