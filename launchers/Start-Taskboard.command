#!/bin/sh
# Finder opens this file in Terminal after the user extracts the ZIP.
TASKBOARD_PACKAGE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
while :; do
    TASKBOARD_PYTHON=
    for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python; do
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
        1) open 'https://www.python.org/downloads/macos/' ;;
        2) ;;
        0) exit 1 ;;
    esac
done
