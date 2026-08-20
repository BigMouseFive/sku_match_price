#!/bin/bash
set -e

# 自更新脚本：拉取最新代码并重新部署服务
# 适用于 macOS

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_CMD="$VENV_DIR/bin/python"
PLIST_LABEL="com.kimi.sku-match-price"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

echo "=========================================="
echo " SKU 成本匹配工具 - 自更新"
echo "=========================================="
echo ""

# 1. 进入项目目录
cd "$PROJECT_DIR"

# 2. 拉取最新代码
echo "→ 正在拉取最新代码..."
if ! git pull origin main; then
    echo "错误：git pull 失败，请检查网络或 SSH 密钥。"
    exit 1
fi
echo "✓ 代码已更新"

# 3. 重新安装依赖
echo "→ 正在更新依赖..."
if [ ! -d "$VENV_DIR" ]; then
    echo "→ 虚拟环境不存在，正在创建..."
    python3 -m venv "$VENV_DIR"
fi
"$PYTHON_CMD" -m pip install --upgrade pip -q
"$PYTHON_CMD" -m pip install -r "$PROJECT_DIR/requirements.txt" -q
echo "✓ 依赖已更新"

# 4. 重启服务
echo "→ 正在重启服务..."
if [ -f "$PLIST_PATH" ]; then
    launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
    launchctl load -w "$PLIST_PATH"
    echo "✓ 服务已重启"
else
    echo "警告：未找到 LaunchAgent 配置，跳过服务重启。"
    echo "如需安装服务，请运行 ./install.sh"
fi

echo ""
echo "=========================================="
echo " 更新完成"
echo "=========================================="
echo "访问地址: http://127.0.0.1:5003"
echo ""
echo "常用命令："
echo "  查看状态: launchctl list | grep $PLIST_LABEL"
echo "  查看日志: tail -f $PROJECT_DIR/logs/stderr.log"
echo ""
