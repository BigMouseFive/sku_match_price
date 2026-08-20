# SKU 成本匹配工具

一个本地运行的 Web 工具，用于把 Amazon 促销/Deal 源表中的 SKU 与库存价值表进行匹配，自动提取成本、运营、小组等信息。

## 功能

- 上传源表（`副本Doula BTS Suppression+Not on Deal.xlsx`）和库存价值表（`库存价值(0601).xlsx`）
- 自动从 `merchant_item_sku` / `merchant_sku` 中提取 SKU
- 根据 `marketplace` 自动识别站点：
  - `AE` / `338801` → 迪拜站点
  - `SA` → 沙特站点
- 按站点匹配指定仓库的最高成本价
  - 迪拜：`迪拜W18-SF仓`、`迪拜FBA仓`
  - 沙特：`沙特YB-SF仓`、`沙特FBA仓`、`沙特老-SF仓`
- 保留原表 5 个 sheet 结构和所有有效列
- 输出右侧附加：`SKU`、`运营`、`小组`、`成本`、`分类信息`、`活跃度`
- 每个 sheet 按 `运营` 升序排序
- 任务队列 + 实时进度展示
- 本地持久化最近 10 个任务及结果文件
- macOS 一键安装 + 开机自启动

## 环境要求

- macOS
- Python 3.11+

## 一键安装（推荐）

```bash
git clone git@github.com:BigMouseFive/sku_match_price.git
cd sku_match_price
./install.sh
```

安装完成后，服务会自动在后台运行，并设置为开机自启动：

```text
http://127.0.0.1:5003
```

## 一键更新

项目目录下执行：

```bash
./update.sh
```

该脚本会：

1. `git pull origin main` 拉取最新代码
2. 更新 `.venv` 中的 Python 依赖
3. 重启 launchd 服务

## 手动运行

```bash
cd sku_match_price
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

然后访问 `http://127.0.0.1:5003`。

## 服务管理

```bash
# 查看状态
launchctl list | grep com.kimi.sku-match-price

# 停止服务
launchctl unload -w ~/Library/LaunchAgents/com.kimi.sku-match-price.plist

# 启动服务
launchctl load -w ~/Library/LaunchAgents/com.kimi.sku-match-price.plist

# 卸载服务
rm ~/Library/LaunchAgents/com.kimi.sku-match-price.plist && launchctl remove com.kimi.sku-match-price
```

## 项目结构

```
sku_match_price/
├── app.py                 # Flask 后端 + 任务队列
├── install.sh             # macOS 一键安装脚本
├── update.sh              # macOS 一键更新脚本
├── requirements.txt       # Python 依赖
├── templates/
│   ├── index.html         # 上传页 + 任务列表
│   └── task.html          # 任务进度页
├── data/                  # 本地持久化数据（运行时生成，不提交）
│   ├── tasks.json
│   └── results/
├── logs/                  # 服务日志（运行时生成，不提交）
├── .venv/                 # 虚拟环境（不提交）
├── .gitignore
└── README.md
```

## 依赖

- Flask
- pandas
- openpyxl
