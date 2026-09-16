from __future__ import annotations

import aiosqlite


class Database:
    def __init__(self, path: str = "shop.db") -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
                    joined_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    description TEXT NOT NULL, price INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL, price INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new', proof_type TEXT,
                    proof_value TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(product_id) REFERENCES products(id)
                );
            """)
            count = (await (await db.execute("SELECT COUNT(*) FROM products")).fetchone())[0]
            if not count:
                await db.executemany(
                    "INSERT INTO products(name, description, price) VALUES(?,?,?)",
                    [
                        ("ChatGPT Plus — 1 месяц", "Активация подписки на вашем аккаунте.", 1990),
                        ("Claude Pro — 1 месяц", "Доступ к расширенным лимитам Claude.", 2190),
                        ("Midjourney Basic — 1 месяц", "Базовый тариф генерации изображений.", 1490),
                    ],
                )
            await db.commit()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(sql, params)
            await db.commit()
            return cur.lastrowid

    async def one(self, sql: str, params: tuple = ()):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            return await (await db.execute(sql, params)).fetchone()

    async def all(self, sql: str, params: tuple = ()):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            return await (await db.execute(sql, params)).fetchall()

    async def upsert_user(self, user) -> None:
        await self.execute(
            "INSERT INTO users(id,username,full_name) VALUES(?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name",
            (user.id, user.username, user.full_name),
        )

