#!/bin/sh
# No task content is interpolated. All task metadata stays in request.json.
if [ ! -t 0 ] && [ "${TASKBOARD_TERMINAL_OPENED:-}" != 1 ]; then
    export TASKBOARD_TERMINAL_OPENED=1
    if command -v x-terminal-emulator >/dev/null 2>&1; then exec x-terminal-emulator -e sh "$0"; fi
    if command -v gnome-terminal >/dev/null 2>&1; then exec gnome-terminal -- sh "$0"; fi
    if command -v konsole >/dev/null 2>&1; then exec konsole -e sh "$0"; fi
    if command -v xterm >/dev/null 2>&1; then exec xterm -e sh "$0"; fi
fi
TASKBOARD_PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
while :; do
    TASKBOARD_PYTHON=
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -I -X utf8 -c 'import sys;sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
            TASKBOARD_PYTHON=$candidate
            break
        fi
    done
    if [ -n "$TASKBOARD_PYTHON" ]; then
        "$TASKBOARD_PYTHON" -I -B -X utf8 "$TASKBOARD_PACKAGE_DIR/bootstrap.py" "$TASKBOARD_PACKAGE_DIR/request.json"
        TASKBOARD_EXIT=$?
        printf '\n按回车关闭 / Press Enter to close: '
        read -r TASKBOARD_CLOSE
        exit "$TASKBOARD_EXIT"
    fi
    printf '\nTaskboard 需要 Python 3.11 或更新版本。\n1 打开官方安装说明  2 安装后重新检查  0 取消\nPython 3.11+ is required. 1 Official installer  2 Retry  0 Cancel\n选择 / Choice: '
    read -r TASKBOARD_CHOICE || exit 1
    case "$TASKBOARD_CHOICE" in
        1) if command -v xdg-open >/dev/null 2>&1; then xdg-open 'https://www.python.org/downloads/' >/dev/null 2>&1; else printf '\nhttps://www.python.org/downloads/\n'; fi ;;
        2) ;;
        0) exit 1 ;;
    esac
done
