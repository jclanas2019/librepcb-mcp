#!/usr/bin/env bash
# Arranca el servidor MCP de LibrePCB.
#
#   ./start.sh            Inicia el servidor (lo usa Claude Desktop)
#   ./start.sh --setup    Solo crea/actualiza el entorno virtual
#   ./start.sh --install  Escribe la config de Claude Desktop automáticamente
#   ./start.sh --install-freerouting  Descarga Freerouting 2.0.1 (versión probada) y Java propio
#   ./start.sh --install-java  Descarga un Java 21 portátil en tools/ (no toca el del sistema)
#   ./start.sh --config   Imprime el bloque JSON para claude_desktop_config.json
#   ./start.sh --check    Verifica Python, el SDK y librepcb-cli
#
# Importante: en modo servidor, stdout es el canal MCP. Todo mensaje va a stderr.

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$DIR/.venv"
PY="$VENV/bin/python"

# Valores por defecto (se pueden sobrescribir desde "env" en la config de Claude)
export LIBREPCB_PROJECTS_DIR="${LIBREPCB_PROJECTS_DIR:-$HOME/LibrePCB-Workspace/projects}"
export LIBREPCB_TEMPLATE="${LIBREPCB_TEMPLATE:-$LIBREPCB_PROJECTS_DIR/plantilla}"

log() { echo "[librepcb-mcp] $*" >&2; }

find_python() {
  # Claude Desktop no carga tu PATH, así que se prueban rutas típicas de macOS
  for c in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

setup() {
  if [[ ! -x "$PY" ]]; then
    local base
    base="$(find_python)" || { log "Necesitas Python 3.10 o superior (brew install python)."; exit 1; }
    log "Creando entorno virtual con $base ..."
    "$base" -m venv "$VENV" >&2
  fi
  if ! "$PY" -c 'import mcp' 2>/dev/null; then
    log "Instalando dependencias ..."
    "$PY" -m pip install -q --upgrade pip >&2
    "$PY" -m pip install -q -r "$DIR/requirements.txt" >&2
  fi
}

case "${1:-}" in
  --setup)
    setup; log "Listo."
    ;;
  --install)
    setup
    CONFIG_FILE="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
    mkdir -p "$(dirname "$CONFIG_FILE")"
    [[ -f "$CONFIG_FILE" ]] && cp "$CONFIG_FILE" "$CONFIG_FILE.bak"
    "$PY" - "$CONFIG_FILE" "$DIR/start.sh" "$LIBREPCB_PROJECTS_DIR" "$LIBREPCB_TEMPLATE" <<'PYEOF'
import json, sys
path, cmd, projects, template = sys.argv[1:]
try:
    cfg = json.load(open(path))
except (FileNotFoundError, json.JSONDecodeError):
    cfg = {}
cfg.setdefault("mcpServers", {})["librepcb"] = {
    "command": cmd,
    "env": {"LIBREPCB_PROJECTS_DIR": projects, "LIBREPCB_TEMPLATE": template},
}
json.dump(cfg, open(path, "w"), indent=2)
PYEOF
    mkdir -p "$LIBREPCB_PROJECTS_DIR"
    log "Configuración escrita en: $CONFIG_FILE"
    [[ -f "$CONFIG_FILE.bak" ]] && log "Respaldo anterior: $CONFIG_FILE.bak"
    log "Ahora cierra Claude Desktop (Cmd+Q) y vuelve a abrirlo."
    ;;
  --install-freerouting)
    setup
    mkdir -p "$DIR/tools"
    FR="https://github.com/freerouting/freerouting/releases/download/v2.0.1/freerouting-2.0.1.jar"
    if [[ ! -f "$DIR/tools/freerouting-2.0.1.jar" ]]; then
      curl -fL --progress-bar -o "$DIR/tools/freerouting-2.0.1.jar" "$FR" >&2
    fi
    log "Freerouting listo: tools/freerouting-2.0.1.jar (versión probada con este servidor)"
    "$0" --install-java
    ;;
  --install-java)
    mkdir -p "$DIR/tools"
    case "$(uname -s)-$(uname -m)" in
      Darwin-arm64) PLAT="mac-aarch64"; JURL="OpenJDK21U-jre_aarch64_mac_hotspot_21.0.8_9.tar.gz" ;;
      Darwin-*)     PLAT="mac-x64";     JURL="OpenJDK21U-jre_x64_mac_hotspot_21.0.8_9.tar.gz" ;;
      Linux-aarch64) PLAT="linux-aarch64"; JURL="OpenJDK21U-jre_aarch64_linux_hotspot_21.0.8_9.tar.gz" ;;
      *)            PLAT="linux-x64";   JURL="OpenJDK21U-jre_x64_linux_hotspot_21.0.8_9.tar.gz" ;;
    esac
    TGZ="$DIR/tools/jre21-$PLAT.tar.gz"
    if [[ ! -f "$TGZ" ]]; then
      log "Descargando Java 21 portátil ($PLAT) ..."
      curl -fL --progress-bar -o "$TGZ" \
        "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.8%2B9/$JURL" >&2
    fi
    rm -rf "$DIR/tools/jre21-$PLAT"
    mkdir -p "$DIR/tools/jre21-$PLAT"
    tar xzf "$TGZ" -C "$DIR/tools/jre21-$PLAT"
    command -v xattr >/dev/null && xattr -dr com.apple.quarantine "$DIR/tools/jre21-$PLAT" 2>/dev/null || true
    J="$(find "$DIR/tools/jre21-$PLAT" -path '*/bin/java' -type f | head -1)"
    "$J" -version >&2 && log "Java listo: $J"
    ;;
  --config)
    cat <<EOF
{
  "mcpServers": {
    "librepcb": {
      "command": "$DIR/start.sh",
      "env": {
        "LIBREPCB_PROJECTS_DIR": "$LIBREPCB_PROJECTS_DIR",
        "LIBREPCB_TEMPLATE": "$LIBREPCB_TEMPLATE"
      }
    }
  }
}
EOF
    ;;
  --check)
    setup
    "$PY" --version >&2
    "$PY" -c 'import importlib.metadata as m; print("mcp", m.version("mcp"))' >&2
    "$PY" -c "import sys; sys.path.insert(0, '$DIR'); import librepcb_mcp as s; print('librepcb-cli:', s.CLI); print(s._run_cli(['--version'])); import librepcb_route as r; print('Ruteo:', r.find_router(s.SERVER_DIR)[2])" >&2
    log "Proyectos: $LIBREPCB_PROJECTS_DIR"
    if [[ -d "$LIBREPCB_TEMPLATE" ]]; then log "Plantilla: $LIBREPCB_TEMPLATE"
    else log "Plantilla no encontrada: $LIBREPCB_TEMPLATE (créala en LibrePCB con File > New Project)"; fi
    ;;
  "")
    setup
    exec "$PY" "$DIR/librepcb_mcp.py"
    ;;
  *)
    log "Opción desconocida: $1  (usa --install, --install-freerouting, --install-java, --setup, --config o --check)"; exit 1
    ;;
esac
