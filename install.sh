#!/bin/bash
set -e

# 一键安装脚本：SKU 成本匹配 Web 工具
# 适用于 macOS，会配置开机自启动（launchd）

APP_NAME="sku_match_price"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_CMD="${VENV_DIR}/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
PLIST_LABEL="com.kimi.sku-match-price"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

echo "=========================================="
echo " SKU 成本匹配工具 - macOS 一键安装"
echo "=========================================="
echo ""

# 1. 检查/安装 uv
if ! command -v uv &> /dev/null; then
    echo "→ 未找到 uv，正在安装..."
    curl -LsSf https://mirrors.tuna.tsinghua.edu.cn/astral-sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
    if ! command -v uv &> /dev/null; then
        echo "错误：uv 安装失败，请手动安装后重试。"
        echo "参考：https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
fi

UV_VERSION=$(uv --version 2>&1 | awk '{print $2}')
echo "✓ 检测到 uv 版本: $UV_VERSION"

# 配置 uv 使用中国镜像下载 Python（默认阿里云，可通过环境变量覆盖）
UV_PYTHON_INSTALL_MIRROR="${UV_PYTHON_INSTALL_MIRROR:-https://mirrors.aliyun.com/astral-sh/python-build-standalone}"

# 2. 使用 uv 安装 Python 3.11（镜像失败时自动回退官方源）
echo "→ 正在安装 Python 3.11..."
if UV_PYTHON_INSTALL_MIRROR="$UV_PYTHON_INSTALL_MIRROR" uv python install 3.11; then
    echo "✓ 通过镜像安装成功"
else
    echo "⚠ 镜像安装失败（$UV_PYTHON_INSTALL_MIRROR），正在回退到官方源..."
    uv python install 3.11 || {
        echo "错误：Python 3.11 安装失败，请检查网络连接。"
        exit 1
    }
fi
UV_PYTHON=$(uv python find 3.11)
echo "✓ Python 路径: $UV_PYTHON"

# 3. 创建/更新虚拟环境
NEED_CREATE_VENV=0
if [ -d "$VENV_DIR" ]; then
    if [ -f "$PYTHON_CMD" ]; then
        VENV_PY_VERSION=$("$PYTHON_CMD" --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
        if [ "$VENV_PY_VERSION" != "3.11" ]; then
            echo "⚠ 现有虚拟环境 Python 版本为 $VENV_PY_VERSION，需要重建为 3.11..."
            rm -rf "$VENV_DIR"
            NEED_CREATE_VENV=1
        else
            echo "✓ 虚拟环境已存在且版本正确: $VENV_DIR"
        fi
    else
        rm -rf "$VENV_DIR"
        NEED_CREATE_VENV=1
    fi
else
    NEED_CREATE_VENV=1
fi

if [ "$NEED_CREATE_VENV" -eq 1 ]; then
    echo "→ 正在创建虚拟环境..."
    uv venv --python "$UV_PYTHON" "$VENV_DIR"
    echo "✓ 虚拟环境创建完成"
fi

# 4. 安装依赖（默认使用中国 PyPI 镜像，可通过环境变量覆盖）
PIP_MIRROR="${PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"
echo "→ 正在安装依赖（镜像: $PIP_MIRROR）..."
uv pip install --python "$PYTHON_CMD" --upgrade pip -q --index-url "$PIP_MIRROR"
uv pip install --python "$PYTHON_CMD" -r "$PROJECT_DIR/requirements.txt" -q --index-url "$PIP_MIRROR"
echo "✓ 依赖安装完成"

# 5. 创建日志目录
mkdir -p "$LOG_DIR"
echo "✓ 日志目录: $LOG_DIR"

# 6. 创建 LaunchAgent plist
echo "→ 正在配置开机自启动..."
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_CMD}</string>
        <string>${PROJECT_DIR}/app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/stderr.log</string>
</dict>
</plist>
EOF

echo "✓ 开机自启动配置已写入: $PLIST_PATH"

# 7. 加载并启动服务
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

echo "✓ 服务已加载并启动"
echo ""
echo "=========================================="
echo " 安装完成"
echo "=========================================="
echo "访问地址: http://0.0.0.0:5003 或 http://<本机IP>:5003"
echo "日志文件: $LOG_DIR/stdout.log"
echo "          $LOG_DIR/stderr.log"
echo ""
echo "常用命令："
echo "  查看状态: launchctl list | grep $PLIST_LABEL"
echo "  停止服务: launchctl unload -w \"$PLIST_PATH\""
echo "  启动服务: launchctl load -w \"$PLIST_PATH\""
echo "  卸载服务: rm \"$PLIST_PATH\" && launchctl remove $PLIST_LABEL"
echo ""
