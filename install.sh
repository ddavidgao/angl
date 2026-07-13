#!/bin/sh
set -eu

REPO="${ANGL_INSTALL_REPO:-https://github.com/ddavidgao/angl.git}"
REF="${ANGL_INSTALL_REF:-main}"
SOURCE="${ANGL_INSTALL_SOURCE:-git+$REPO@$REF}"
PREFIX="${ANGL_INSTALL_PREFIX:-$HOME/.angl}"
BIN_DIR="${ANGL_INSTALL_BIN_DIR:-$HOME/.local/bin}"
VENV="$PREFIX/venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip >/dev/null
echo "installing $SOURCE"
"$VENV/bin/python" -m pip install --upgrade "$SOURCE"

mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/angl" "$BIN_DIR/angl"

echo "installed angl:"
"$BIN_DIR/angl" --version
echo
echo "binary:"
echo "  $BIN_DIR/angl"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo
    echo "add this to your shell profile if angl is not found:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac
