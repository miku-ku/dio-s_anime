"""SQLite 数据库连接与初始化。"""
import sqlite3
from pathlib import Path

# 数据库文件放在 backend/ 目录下，首次启动自动创建
DB_PATH = Path(__file__).parent / "app.db"


def get_db() -> sqlite3.Connection:
    """获取一个数据库连接（行结果可用列名访问）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（如果不存在），并为旧库补充新增列。"""
    conn = get_db()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                avatar        TEXT,
                bio           TEXT DEFAULT '',
                token_version INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        conn.commit()

        # 迁移：旧数据库缺少新增列时逐个补上
        columns = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "avatar" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT")
            conn.commit()
        if "bio" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN bio TEXT DEFAULT ''")
            conn.commit()
        if "token_version" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"
            )
            conn.commit()
    finally:
        conn.close()
