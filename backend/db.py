"""
Cơ sở dữ liệu SQLite cho tài khoản người dùng và lịch sử chấm.

Vị trí file CSDL:
  - DATA_DIR (nếu đặt), hoặc
  - RAILWAY_VOLUME_MOUNT_PATH (Railway tự đặt khi gắn Volume), hoặc
  - thư mục backend (chạy local).

LƯU Ý: Trên Railway, ổ đĩa của container bị xoá mỗi lần deploy.
Phải gắn một Volume cho service, nếu không mọi tài khoản sẽ mất sau mỗi lần deploy.
"""
import json
import logging
import os
import pathlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from thoi_gian import now_vn

log = logging.getLogger("benh_an.db")

BASE_DIR = pathlib.Path(__file__).parent
DATA_DIR = pathlib.Path(
    os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or BASE_DIR
)
DB_PATH = DATA_DIR / "benh_an.db"
LEGACY_HISTORY_FILES = [DATA_DIR / "lich_su_cham.json", BASE_DIR / "lich_su_cham.json"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT    NOT NULL UNIQUE,
    ho_ten         TEXT    NOT NULL,
    password_hash  TEXT    NOT NULL,
    is_verified    INTEGER NOT NULL DEFAULT 0,
    email_ket_qua  INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL,
    verified_at    TEXT,
    last_login_at  TEXT
);

CREATE TABLE IF NOT EXISTS grading_records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT    NOT NULL,
    data        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_user ON grading_records(user_id, id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS admin_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT    NOT NULL,
    admin_id     INTEGER,
    admin_email  TEXT,
    action       TEXT    NOT NULL,
    target       TEXT,
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS idx_admin_log_time ON admin_log(id DESC);
"""

# Cột thêm sau (CSDL cũ được tự nâng cấp khi khởi động)
MIGRATIONS = [
    ("users", "is_admin", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "is_locked", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "locked_at", "TEXT"),
    # Khi xoá tài khoản nhưng giữ lịch sử: lưu "Họ tên <email>" của người chấm đã xoá
    ("grading_records", "deleted_owner", "TEXT"),
]


def now_str() -> str:
    return now_vn().strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        for table, col, ddl in MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                log.info("Nâng cấp CSDL: thêm cột %s.%s", table, col)
    _import_legacy_history()

    on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
    if on_railway and not (os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")):
        log.warning(
            "⚠️  Chưa gắn Volume trên Railway: CSDL đang nằm ở %s và sẽ BỊ XOÁ khi deploy lại. "
            "Hãy gắn một Volume cho service (ví dụ mount tại /data).", DB_PATH
        )
    log.info("CSDL: %s", DB_PATH)


def _import_legacy_history():
    """Nhập lịch sử cũ (lich_su_cham.json, trước khi có tài khoản) một lần duy nhất.
    Các bản ghi này chưa có chủ; sẽ được gán cho admin gốc (ADMIN_EMAILS) đầu tiên đăng nhập."""
    with get_db() as conn:
        done = conn.execute("SELECT value FROM meta WHERE key='legacy_imported'").fetchone()
        if done:
            return
        imported = 0
        for f in LEGACY_HISTORY_FILES:
            if not f.exists():
                continue
            try:
                records = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for r in records if isinstance(records, list) else []:
                try:  # giữ ngày chấm gốc ("dd/mm/YYYY HH:MM") thay vì ngày nhập
                    created = datetime.strptime(r.get("thoi_gian", ""), "%d/%m/%Y %H:%M").strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    created = now_str()
                conn.execute(
                    "INSERT INTO grading_records(user_id, created_at, data) VALUES (NULL, ?, ?)",
                    (created, json.dumps(r, ensure_ascii=False)),
                )
                imported += 1
            break
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('legacy_imported', ?)", (str(imported),)
        )
        if imported:
            log.info("Đã nhập %d bản ghi lịch sử cũ (chưa có chủ).", imported)


def claim_orphan_records(user_id: int) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE grading_records SET user_id=? WHERE user_id IS NULL AND deleted_owner IS NULL",
            (user_id,)
        )
        return cur.rowcount


# ── Lịch sử chấm ──

def _row_to_record(row) -> dict:
    rec = json.loads(row["data"])
    rec["id"] = row["id"]
    return rec


def add_records(user_id: int, results: list) -> list:
    with get_db() as conn:
        for r in results:
            r.pop("id", None)
            cur = conn.execute(
                "INSERT INTO grading_records(user_id, created_at, data) VALUES (?, ?, ?)",
                (user_id, now_str(), json.dumps(r, ensure_ascii=False)),
            )
            r["id"] = cur.lastrowid
    return results


def list_records(user_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, data FROM grading_records WHERE user_id=? ORDER BY id", (user_id,)
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def get_records(user_id: int, ids: list) -> list:
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT id, data FROM grading_records WHERE user_id=? AND id IN ({marks}) ORDER BY id",
            (user_id, *ids),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def delete_records(user_id: int) -> int:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM grading_records WHERE user_id=?", (user_id,))
        return cur.rowcount


# ── Nhật ký quản trị ──

def log_admin(admin, action: str, target: str = "", detail: str = ""):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO admin_log(created_at, admin_id, admin_email, action, target, detail) "
            "VALUES (?,?,?,?,?,?)",
            (now_str(), admin["id"] if admin else None, admin["email"] if admin else None,
             action, target or "", detail or ""),
        )


def recent_admin_log(admin_id: int, action: str, minutes: int) -> bool:
    """Admin này đã có hành động `action` trong `minutes` phút gần đây chưa (để tránh ghi trùng)."""
    since = (now_vn() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM admin_log WHERE admin_id=? AND action=? AND created_at>=? LIMIT 1",
            (admin_id, action, since),
        ).fetchone() is not None


def list_admin_log(limit: int = 200) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM admin_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def backup_to(path) -> None:
    """Sao chép toàn bộ CSDL (an toàn khi app đang chạy) sang file `path`."""
    src = _connect()
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
