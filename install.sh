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

# 1. 检查 Python3
if ! command -v python3 &> /dev/null; then
    echo "错误：未找到 python3，请先安装 Python 3（推荐 3.11+）。"
    echo "可访问 https://www.python.org/downloads/macos/ 下载安装。"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ 检测到 Python 版本: $PYTHON_VERSION"

# 2. 创建/更新虚拟环境
if [ -d "$VENV_DIR" ]; then
    echo "✓ 虚拟环境已存在: $VENV_DIR"
else
    echo "→ 正在创建虚拟环境..."
    python3 -m venv "$VENV_DIR"
    echo "✓ 虚拟环境创建完成"
fi

# 3. 安装依赖
echo "→ 正在安装依赖..."
"$PYTHON_CMD" -m pip install --upgrade pip -q
"$PYTHON_CMD" -m pip install -r "$PROJECT_DIR/requirements.txt" -q
echo "✓ 依赖安装完成"

# 4. 创建日志目录
mkdir -p "$LOG_DIR"
echo "✓ 日志目录: $LOG_DIR"

# 5. 创建 LaunchAgent plist
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

# 6. 加载并启动服务
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

echo "✓ 服务已加载并启动"
echo ""
echo "=========================================="
echo " 安装完成"
echo "=========================================="
echo "访问地址: http://127.0.0.1:5003"
echo "日志文件: $LOG_DIR/stdout.log"
echo "          $LOG_DIR/stderr.log"
echo ""
echo "常用命令："
echo "  查看状态: launchctl list | grep $PLIST_LABEL"
echo "  停止服务: launchctl unload -w \"$PLIST_PATH\""
echo "  启动服务: launchctl load -w \"$PLIST_PATH\""
echo "  卸载服务: rm \"$PLIST_PATH\" && launchctl remove $PLIST_LABEL"
echo ""
