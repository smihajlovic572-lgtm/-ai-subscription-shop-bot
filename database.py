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
                CREATE TABLE IF NOT EXISTS topups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    proof_type TEXT,
                    proof_value TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
            """)
            user_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(users)")).fetchall()}
            if "balance" not in user_columns:
                await db.execute("ALTER TABLE users ADD COLUMN balance INTEGER NOT NULL DEFAULT 0")
            product_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(products)")).fetchall()}
            if "stock" not in product_columns:
                await db.execute("ALTER TABLE products ADD COLUMN stock INTEGER NOT NULL DEFAULT 0")
            count = (await (await db.execute("SELECT COUNT(*) FROM products")).fetchone())[0]
            if not count:
                await db.executemany(
                    "INSERT INTO products(name, description, price, stock) VALUES(?,?,?,?)",
                    [
                        ("ChatGPT Plus — 1 месяц", "Активация подписки на вашем аккаунте.", 1990, 10),
                        ("Claude Pro — 1 месяц", "Доступ к расширенным лимитам Claude.", 2190, 10),
                        ("Midjourney Basic — 1 месяц", "Базовый тариф генерации изображений.", 1490, 10),
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

    async def purchase(self, user_id: int, product_id: int) -> tuple[bool, str, int | None]:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            product = await (await conn.execute("SELECT * FROM products WHERE id=? AND active=1", (product_id,))).fetchone()
            user = await (await conn.execute("SELECT * FROM users WHERE id=?", (user_id,))).fetchone()
            if not product or product["stock"] <= 0:
                await conn.rollback(); return False, "out_of_stock", None
            if not user or user["balance"] < product["price"]:
                await conn.rollback(); return False, "insufficient", None
            await conn.execute("UPDATE users SET balance=balance-? WHERE id=?", (product["price"], user_id))
            await conn.execute("UPDATE products SET stock=stock-1 WHERE id=?", (product_id,))
            cur = await conn.execute(
                "INSERT INTO orders(user_id,product_id,price,status) VALUES(?,?,?,'paid')",
                (user_id, product_id, product["price"]),
            )
            await conn.commit()
            return True, "ok", cur.lastrowid

    async def approve_topup(self, topup_id: int) -> tuple[bool, int | None, int | None]:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("BEGIN IMMEDIATE")
            topup = await (await conn.execute("SELECT * FROM topups WHERE id=?", (topup_id,))).fetchone()
            if not topup or topup["status"] != "paid":
                await conn.rollback(); return False, None, None
            await conn.execute("UPDATE topups SET status='approved' WHERE id=?", (topup_id,))
            await conn.execute("UPDATE users SET balance=balance+? WHERE id=?", (topup["amount"], topup["user_id"]))
            await conn.commit()
            return True, topup["user_id"], topup["amount"]
