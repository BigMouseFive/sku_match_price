import os
import re
import tempfile
import threading
import time
import uuid
import hashlib
import json
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

app = Flask(__name__)
app.secret_key = "sku_match_price_secret"

SKU_RE = re.compile(r"[A-Z]{2}\d{4}[A-Z]?")

UPLOAD_FOLDER = tempfile.mkdtemp(prefix="sku_match_")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

# 本地持久化目录：任务元数据 + 结果文件
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RESULTS_DIR = os.path.join(DATA_DIR, "results")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")
MAX_PERSISTED_TASKS = 10
os.makedirs(RESULTS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# 任务队列与状态管理
# ---------------------------------------------------------------------------
@dataclass
class Task:
    id: str
    source_path: str
    inv_path: str
    source_name: str
    inv_name: str
    status: str = "queued"  # queued / processing / completed / failed
    progress: int = 0
    message: str = "排队中"
    result_file: str = ""
    total_rows: int = 0
    error: str = ""
    created_at: float = field(default_factory=time.time)


def _task_to_dict(task: Task) -> dict:
    """把 Task 对象序列化为可持久化的 dict（去掉临时上传路径）。"""
    return {
        "id": task.id,
        "source_name": task.source_name,
        "inv_name": task.inv_name,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "result_file": task.result_file,
        "total_rows": task.total_rows,
        "error": task.error,
        "created_at": task.created_at,
    }


def _task_from_dict(data: dict) -> Task:
    """从 dict 恢复 Task 对象（临时上传路径留空）。"""
    return Task(
        id=data["id"],
        source_path="",
        inv_path="",
        source_name=data.get("source_name", ""),
        inv_name=data.get("inv_name", ""),
        status=data.get("status", "completed"),
        progress=data.get("progress", 100),
        message=data.get("message", ""),
        result_file=data.get("result_file", ""),
        total_rows=data.get("total_rows", 0),
        error=data.get("error", ""),
        created_at=data.get("created_at", time.time()),
    )


def _save_tasks(tasks: dict[str, Task]):
    """把最近 MAX_PERSISTED_TASKS 个任务保存到本地 JSON。"""
    persisted = sorted(tasks.values(), key=lambda t: t.created_at, reverse=True)[:MAX_PERSISTED_TASKS]
    data = [_task_to_dict(t) for t in persisted]
    try:
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        # 持久化失败不应影响主流程
        pass


def _load_tasks() -> dict[str, Task]:
    """从本地 JSON 加载历史任务。"""
    if not os.path.exists(TASKS_FILE):
        return {}
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {item["id"]: _task_from_dict(item) for item in data if "id" in item}
    except Exception:
        return {}


def _cleanup_old_results(tasks: dict[str, Task]):
    """删除不在最近任务列表中的结果文件。"""
    kept_files = {t.result_file for t in tasks.values() if t.result_file}
    try:
        for fname in os.listdir(RESULTS_DIR):
            if fname not in kept_files:
                os.remove(os.path.join(RESULTS_DIR, fname))
    except Exception:
        pass


class TaskManager:
    def __init__(self):
        self.queue = deque()
        self.lock = threading.Lock()
        # 加载本地持久化的历史任务
        self.tasks = _load_tasks()
        _cleanup_old_results(self.tasks)
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()

    def submit(self, source_path, inv_path, source_name, inv_name) -> str:
        task_id = uuid.uuid4().hex[:12]
        task = Task(
            id=task_id,
            source_path=source_path,
            inv_path=inv_path,
            source_name=source_name,
            inv_name=inv_name,
        )
        with self.lock:
            self.tasks[task_id] = task
            self.queue.append(task_id)
        return task_id

    def get_task(self, task_id: str) -> Task | None:
        with self.lock:
            return self.tasks.get(task_id)

    def get_queue_position(self, task_id: str) -> int:
        """返回任务在队列中的位置，0 表示正在处理，-1 表示不在队列中。"""
        with self.lock:
            if task_id not in self.tasks:
                return -1
            task = self.tasks[task_id]
            if task.status == "processing":
                return 0
            for idx, qid in enumerate(self.queue):
                if qid == task_id:
                    return idx + 1
            return -1

    def get_recent_tasks(self, limit: int = 20) -> list[Task]:
        """返回最近创建的任务列表，按创建时间倒序。"""
        with self.lock:
            tasks = sorted(self.tasks.values(), key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def _update_task(self, task_id: str, **kwargs):
        with self.lock:
            task = self.tasks.get(task_id)
            if task:
                for k, v in kwargs.items():
                    setattr(task, k, v)
                # 任务进入终态时持久化
                if task.status in ("completed", "failed"):
                    _save_tasks(self.tasks)

    def _worker(self):
        while True:
            try:
                task_id = self.queue.popleft()
            except IndexError:
                time.sleep(0.5)
                continue

            task = self.get_task(task_id)
            if not task:
                continue

            self._update_task(
                task_id,
                status="processing",
                progress=0,
                message="开始处理",
            )

            def update(percent, message):
                self._update_task(task_id, progress=min(100, max(0, int(percent))), message=message)

            try:
                result_sheets = process(
                    task.source_path,
                    task.inv_path,
                    progress_callback=update,
                )
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                result_filename = f"SKU成本匹配结果_{timestamp}_{task.id}.xlsx"
                result_path = os.path.join(RESULTS_DIR, result_filename)
                update(95, "正在写入 Excel...")
                with pd.ExcelWriter(result_path, engine="openpyxl") as writer:
                    for sheet_name, df in result_sheets.items():
                        # Excel sheet 名长度限制 31 字符
                        safe_name = sheet_name[:31]
                        df.to_excel(writer, sheet_name=safe_name, index=False)
                update(100, "处理完成")
                total_rows = sum(len(df) for df in result_sheets.values())
                self._update_task(
                    task_id,
                    status="completed",
                    result_file=result_filename,
                    total_rows=total_rows,
                )
            except Exception as e:
                self._update_task(
                    task_id,
                    status="failed",
                    error=str(e),
                    message=f"处理失败：{e}",
                )


task_manager = TaskManager()


# ---------------------------------------------------------------------------
# 业务逻辑
# ---------------------------------------------------------------------------
def extract_sku(text):
    """从 merchant_item_sku 文本中提取 SKU：两个大写字母 + 4 位数字 + 可选大写字母。"""
    if pd.isna(text):
        return None
    m = SKU_RE.search(str(text))
    return m.group(0) if m else None


def read_source_sheets(file_path, progress_callback=None):
    """
    读取源表全部 sheet，保留原表所有列，并在每行后追加提取出的 SKU。
    返回 dict：{sheet_name: DataFrame（含原始列 + SKU 列）}
    """
    xl = pd.ExcelFile(file_path)
    sku_col_candidates = ["merchant_item_sku", "merchant_sku"]
    total_sheets = len(xl.sheet_names)
    sheets = {}

    for i, sheet_name in enumerate(xl.sheet_names):
        if progress_callback:
            pct = 5 + (i / total_sheets) * 15  # 5% -> 20%
            progress_callback(pct, f"正在读取源表 sheet：{sheet_name}")

        df = xl.parse(sheet_name)
        col = next((c for c in df.columns if c in sku_col_candidates), None)
        if col is None:
            sheets[sheet_name] = df.copy()
            continue

        df = df.copy()
        df["SKU"] = df[col].apply(extract_sku)
        sheets[sheet_name] = df

    if progress_callback:
        total_rows = sum(len(df) for df in sheets.values())
        progress_callback(30, f"源表读取完成，共 {total_sheets} 个 sheet，{total_rows} 行")

    return sheets


def build_team_map(file_path, progress_callback=None):
    """读取团队 sheet，返回 销售员 -> 小组 的字典。"""
    df = pd.read_excel(file_path, sheet_name="团队")
    team_map = (
        df.dropna(subset=["销售员", "团队"])
        .set_index("销售员")["团队"]
        .to_dict()
    )
    if progress_callback:
        progress_callback(32, "团队映射读取完成")
    return team_map


def aggregate_cost_sheet(
    file_path,
    sheet_name,
    warehouse_filter=None,
):
    """
    对单个成本 sheet 按 SKU 聚合：
    - 仓库成本价取最大值
    - 销售员、分类信息、活跃度取第一个非空值

    warehouse_filter: 允许的仓库名称列表，None 表示不过滤。
    使用 pandas 向量化 groupby，速度远快于逐行迭代。
    """
    df = pd.read_excel(file_path, sheet_name=sheet_name)

    if warehouse_filter is not None and "仓库名称" in df.columns:
        df = df[df["仓库名称"].isin(warehouse_filter)]

    if "仓库成本价" in df.columns:
        df["仓库成本价"] = pd.to_numeric(df["仓库成本价"], errors="coerce")

    agg_spec = {}
    if "销售员" in df.columns:
        agg_spec["销售员"] = ("销售员", "first")
    if "分类信息" in df.columns:
        agg_spec["分类信息"] = ("分类信息", "first")
    if "活跃度" in df.columns:
        agg_spec["活跃度"] = ("活跃度", "first")
    if "仓库成本价" in df.columns:
        agg_spec["仓库成本价"] = ("仓库成本价", "max")

    if not agg_spec:
        return pd.DataFrame(columns=["库存SKU"])

    agg = (
        df.groupby("库存SKU", as_index=False)
        .agg(**agg_spec)
        .rename(columns={"库存SKU": "库存SKU"})
    )

    out_cols = ["库存SKU"] + [c for c in ["销售员", "分类信息", "活跃度", "仓库成本价"] if c in agg.columns]
    return agg[out_cols]





# 各站点允许参与成本比较的仓库
DUBAI_WAREHOUSES = ["迪拜W18-SF仓", "迪拜FBA仓"]
SAUDI_WAREHOUSES = ["沙特YB-SF仓", "沙特FBA仓", "沙特老-SF仓"]

# 库存表聚合结果缓存：{文件路径+mtime: {dubai: df, saudi: df}}
_inventory_cache = {}
_inventory_cache_lock = threading.Lock()


def _get_cache_key(inv_path: str) -> str:
    """基于文件内容生成缓存 key，避免同名文件重新上传后 mtime 不同导致缓存失效。"""
    size = os.path.getsize(inv_path)
    hasher = hashlib.md5()
    hasher.update(str(size).encode())
    with open(inv_path, "rb") as f:
        # 取文件头部 1MB
        hasher.update(f.read(1024 * 1024))
        # 取文件尾部 1MB
        if size > 2 * 1024 * 1024:
            f.seek(-1024 * 1024, 2)
            hasher.update(f.read())
    return f"{inv_path}:{hasher.hexdigest()}"


def _load_single_site(inv_path, sheet_name, warehouses, team_map):
    """加载并聚合单个站点的库存表。用于线程池并行调用。"""
    df = aggregate_cost_sheet(inv_path, sheet_name, warehouse_filter=warehouses)
    df = df.rename(columns={"仓库成本价": "成本", "库存SKU": "SKU"})
    df["小组"] = df["销售员"].map(team_map)
    return df


def build_inventory_table(inv_path, progress_callback=None):
    """
    构建两张库存匹配表：一张 Dubai 站点、一张 Saudi 站点。
    使用线程池并行读取两个 sheet，并缓存结果避免重复计算。
    返回 dict：{
        "dubai": DataFrame(SKU, 销售员, 成本, 分类信息, 活跃度, 小组),
        "saudi": DataFrame(...),
    }
    """
    cache_key = _get_cache_key(inv_path)
    with _inventory_cache_lock:
        if cache_key in _inventory_cache:
            if progress_callback:
                progress_callback(65, "使用缓存的库存表")
            return _inventory_cache[cache_key]

    if progress_callback:
        progress_callback(35, "正在读取库存成本 sheet...")

    team_map = build_team_map(inv_path, progress_callback)

    # 并行读取迪拜、沙特两个 sheet
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_dubai = executor.submit(
            _load_single_site,
            inv_path,
            "迪拜任意仓成本价",
            DUBAI_WAREHOUSES,
            team_map,
        )
        future_saudi = executor.submit(
            _load_single_site,
            inv_path,
            "沙特任意仓成本价",
            SAUDI_WAREHOUSES,
            team_map,
        )
        inv_dubai = future_dubai.result()
        inv_saudi = future_saudi.result()

    result = {"dubai": inv_dubai, "saudi": inv_saudi}

    with _inventory_cache_lock:
        _inventory_cache[cache_key] = result

    if progress_callback:
        progress_callback(65, "库存表构建完成")

    return result


def detect_site(row, sku_value=None):
    """根据源表行的 marketplace 列或 SKU 文本判断站点。"""
    # 优先使用 marketplace / marketplace_id 列
    mp = None
    for col in ["marketplace", "marketplace_id"]:
        if col in row.index and pd.notna(row[col]):
            mp = str(row[col]).strip().upper()
            break
    if mp in ("AE", "338801"):
        return "dubai"
    if mp == "SA":
        return "saudi"

    # 没有 marketplace 列时，从 SKU 文本推断（如 #KSA02-FBA / #UAE03-FBA）
    if sku_value and isinstance(sku_value, str):
        sku_upper = sku_value.upper()
        if "KSA" in sku_upper:
            return "saudi"
        if "UAE" in sku_upper:
            return "dubai"

    return "dubai"  # 默认兜底为迪拜


def _is_unnamed_column(col):
    """判断列名是否为 Excel 读取时产生的空列名。"""
    if not isinstance(col, str):
        return True
    col = col.strip()
    return col == "" or col.lower().startswith("unnamed:")


def _process_one_sheet(sheet_name, df, inv_combined):
    """处理单个源表 sheet，用于线程池并行调用。"""
    df = df.copy()

    # 去掉源表中的空列 / Unnamed 列
    df = df[[c for c in df.columns if not _is_unnamed_column(c)]]

    # 定位原始 SKU 列，用于站点推断
    sku_col_candidates = ["merchant_item_sku", "merchant_sku"]
    sku_col = next((c for c in df.columns if c in sku_col_candidates), None)

    df["_site"] = df.apply(
        lambda row: detect_site(row, sku_value=row.get(sku_col) if sku_col else None),
        axis=1,
    )

    # 按 SKU + 站点 合并库存信息
    merged = df.merge(inv_combined, on=["SKU", "_site"], how="left")
    merged = merged.drop(columns=["_site"])

    # 把匹配列重命名为用户需要的名称
    merged = merged.rename(columns={"销售员": "运营"})

    # 调整列顺序：原表所有列 + SKU + 运营 + 小组 + 成本 + 分类信息 + 活跃度
    original_cols = [c for c in df.columns if c not in ("SKU", "_site")]
    match_cols = ["SKU", "运营", "小组", "成本", "分类信息", "活跃度"]
    final_cols = original_cols + [c for c in match_cols if c not in original_cols]
    merged = merged[final_cols]

    # 按 运营 排序，空值放最后
    merged = merged.sort_values(
        by="运营", na_position="last", key=lambda s: s.astype(str)
    ).reset_index(drop=True)

    return sheet_name, merged


def process(source_path, inv_path, progress_callback=None):
    """
    主处理流程：
    1. 读取源表每个 sheet，保留原表所有列，追加提取的 SKU。
    2. 根据每行的 marketplace 判断站点，用对应站点的库存表匹配。
    3. 追加 运营、小组、成本、分类信息、活跃度。
    4. 每个 sheet 按 运营 升序排序。
    返回 dict：{sheet_name: result_df}
    """
    if progress_callback:
        progress_callback(0, "任务开始")

    source_sheets = read_source_sheets(source_path, progress_callback)
    inv_by_site = build_inventory_table(inv_path, progress_callback)

    # 构建带站点标识的统一库存表
    inv_combined = pd.concat(
        [
            inv_by_site["dubai"].assign(_site="dubai"),
            inv_by_site["saudi"].assign(_site="saudi"),
        ],
        ignore_index=True,
    )

    if progress_callback:
        progress_callback(65, "正在匹配 SKU...")

    result_sheets = {}
    total_sheets = len(source_sheets)

    # 使用线程池并行处理 5 个 sheet
    with ThreadPoolExecutor(max_workers=min(5, total_sheets)) as executor:
        futures = {
            executor.submit(_process_one_sheet, sheet_name, df, inv_combined): sheet_name
            for sheet_name, df in source_sheets.items()
        }
        completed = 0
        for future in futures:
            sheet_name, merged = future.result()
            result_sheets[sheet_name] = merged
            completed += 1
            if progress_callback:
                pct = 65 + completed / total_sheets * 25  # 65% -> 90%
                progress_callback(pct, f"正在处理 sheet：{sheet_name}")

    if progress_callback:
        progress_callback(90, "匹配完成")

    return result_sheets


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        source_file = request.files.get("source_file")
        inv_file = request.files.get("inv_file")

        if not source_file or not source_file.filename:
            flash("请上传源表（副本Doula BTS...）")
            return redirect(request.url)
        if not inv_file or not inv_file.filename:
            flash("请上传库存价值表")
            return redirect(request.url)

        source_path = os.path.join(
            app.config["UPLOAD_FOLDER"], "source_" + source_file.filename
        )
        inv_path = os.path.join(
            app.config["UPLOAD_FOLDER"], "inv_" + inv_file.filename
        )
        source_file.save(source_path)
        inv_file.save(inv_path)

        task_id = task_manager.submit(
            source_path,
            inv_path,
            source_file.filename,
            inv_file.filename,
        )
        return redirect(url_for("task_status", task_id=task_id))

    recent_tasks = task_manager.get_recent_tasks()
    return render_template("index.html", tasks=recent_tasks)


@app.route("/task/<task_id>")
def task_status(task_id):
    task = task_manager.get_task(task_id)
    if not task:
        flash("任务不存在")
        return redirect(url_for("index"))
    return render_template("task.html", task_id=task_id)


@app.route("/api/task/<task_id>")
def api_task_status(task_id):
    task = task_manager.get_task(task_id)
    if not task:
        return jsonify({"error": "任务不存在"}), 404
    queue_pos = task_manager.get_queue_position(task_id)
    return jsonify(
        {
            "id": task.id,
            "status": task.status,
            "progress": task.progress,
            "message": task.message,
            "total_rows": task.total_rows,
            "result_file": task.result_file,
            "error": task.error,
            "queue_position": queue_pos,
        }
    )


@app.route("/download/<filename>")
def download(filename):
    path = os.path.join(RESULTS_DIR, filename)
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5003)
