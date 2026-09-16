from __future__ import annotations

import asyncio
import logging
import os
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

from database import Database
from emojis import ce, eid

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "")
def parse_admin_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for value in raw.split(","):
        value = value.strip()
        if value and value.lstrip("-").isdigit():
            result.add(int(value))
    return result


ADMIN_IDS = parse_admin_ids(os.getenv("ADMIN_IDS", ""))
SUPPORT = os.getenv("SUPPORT_USERNAME", "@support")
PAYMENT_DETAILS = os.getenv("PAYMENT_DETAILS", "Укажите реквизиты в .env")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Gpt_Astra6f_bot").lstrip("@")

router = Router()
db = Database()


class Flow(StatesGroup):
    payment_proof = State()
    delivery = State()
    product = State()
    broadcast = State()
    topup_amount = State()
    topup_proof = State()
    product_stock = State()
    product_price = State()


def btn(text: str, icon: str, **kwargs) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, icon_custom_emoji_id=eid(icon), **kwargs)


def home_kb(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [btn("Каталог", "box", callback_data="catalog")],
        [btn("Мои заказы", "file", callback_data="orders"), btn("Профиль", "profile", callback_data="profile")],
        [btn("Поддержка", "info", url=f"https://t.me/{SUPPORT.lstrip('@')}")],
    ]
    if user_id in ADMIN_IDS:
        rows.append([btn("Админ-панель", "settings", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back(target: str = "home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn("Назад", "down", callback_data=target)]])


async def edit_or_send(event: Message | CallbackQuery, text: str, markup=None) -> None:
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except Exception:
            await event.message.answer(text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db.upsert_user(message.from_user)
    if message.text and message.text.startswith("/start product_"):
        raw_id = message.text.removeprefix("/start product_")
        if raw_id.isdigit():
            p = await db.one("SELECT * FROM products WHERE id=? AND active=1", (int(raw_id),))
            if p:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [btn("Купить", "wallet", callback_data=f"buy:{p['id']}")],
                    [btn("Главное меню", "home", callback_data="home")],
                ])
                await message.answer(
                    f"<b>{ce('tag')} {escape(p['name'])}</b>\n\n{escape(p['description'])}\n\n"
                    f"Цена: <b>{p['price']} ₽</b>\nВ наличии: <b>{p['stock']} шт.</b>",
                    reply_markup=kb,
                )
                return
    welcome_text = (
        f"<b>{ce('bot')} Добро пожаловать в магазин AI-подписок!</b>\n\n"
        "Здесь можно оформить доступ к популярным нейросетям быстро и удобно.\n\n"
        f"{ce('box')} Выберите нужный сервис в каталоге.\n"
        f"{ce('wallet')} Оплатите заказ по указанным реквизитам.\n"
        f"{ce('attach')} Отправьте чек — менеджер проверит оплату и выдаст подписку."
    )
    try:
        await message.answer_photo(
            photo=FSInputFile("welcome.png"),
            caption=welcome_text,
            reply_markup=home_kb(message.from_user.id),
        )
    except OSError:
        await message.answer(welcome_text, reply_markup=home_kb(message.from_user.id))


@router.callback_query(F.data == "home")
async def home(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit_or_send(callback, f"<b>{ce('home')} Главное меню</b>\n\nВыберите раздел:", home_kb(callback.from_user.id))


@router.callback_query(F.data == "catalog")
async def catalog(callback: CallbackQuery) -> None:
    products = await db.all("SELECT * FROM products WHERE active=1 ORDER BY id")
    rows = [[btn(f"{p['name']} · {p['price']} ₽ · {p['stock']} шт.", "tag", callback_data=f"product:{p['id']}")] for p in products]
    rows.append([btn("Назад", "down", callback_data="home")])
    await edit_or_send(callback, f"<b>{ce('box')} Каталог</b>\n\nВыберите подписку:", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("product:"))
async def product(callback: CallbackQuery) -> None:
    product_id = int(callback.data.split(":")[1])
    p = await db.one("SELECT * FROM products WHERE id=? AND active=1", (product_id,))
    if not p:
        await callback.answer("Товар недоступен", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Купить с баланса", "wallet", callback_data=f"buy:{p['id']}")],
        [btn("Ссылка на товар", "link", url=f"https://t.me/{BOT_USERNAME}?start=product_{p['id']}")],
        [btn("Назад", "down", callback_data="catalog")],
    ])
    await edit_or_send(callback, f"<b>{ce('tag')} {escape(p['name'])}</b>\n\n{escape(p['description'])}\n\n<b>Цена: {p['price']} ₽</b>\nВ наличии: <b>{p['stock']} шт.</b>", kb)


@router.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery, state: FSMContext) -> None:
    product_id = int(callback.data.split(":")[1])
    p = await db.one("SELECT * FROM products WHERE id=? AND active=1", (product_id,))
    if not p:
        await callback.answer("Товар недоступен", show_alert=True)
        return
    ok, reason, order_id = await db.purchase(callback.from_user.id, product_id)
    if not ok:
        if reason == "out_of_stock":
            await callback.answer("Товар закончился", show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [btn("Пополнить баланс", "coin", callback_data="topup")],
            [btn("Назад", "down", callback_data=f"product:{product_id}")],
        ])
        await edit_or_send(callback, f"<b>{ce('wallet')} Недостаточно средств</b>\n\nПополните баланс и повторите покупку.", kb)
        return
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Подтвердить", "ok", callback_data=f"approve:{order_id}"), btn("Отклонить", "cancel", callback_data=f"reject:{order_id}")]
    ])
    for admin_id in ADMIN_IDS:
        try:
            await callback.bot.send_message(
                admin_id,
                f"<b>{ce('bell')} Новый заказ №{order_id}</b>\n\n"
                f"Клиент: <a href=\"tg://user?id={callback.from_user.id}\">{escape(callback.from_user.full_name)}</a>\n"
                f"Товар: {escape(p['name'])}\nСумма списана: {p['price']} ₽",
                reply_markup=admin_kb,
            )
        except TelegramForbiddenError:
            pass
    await edit_or_send(callback, f"<b>{ce('ok')} Заказ №{order_id} оплачен с баланса</b>\n\nМенеджер подготовит подписку.", home_kb(callback.from_user.id))


@router.callback_query(F.data == "cancel_order")
async def cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("order_id"):
        await db.execute("UPDATE orders SET status='cancelled' WHERE id=? AND status='new'", (data["order_id"],))
    await state.clear()
    await home(callback, state)


@router.message(Flow.payment_proof)
async def proof(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    order_id = data["order_id"]
    proof_type, proof_value = "text", message.text or message.caption or "Подтверждение"
    if message.photo:
        proof_type, proof_value = "photo", message.photo[-1].file_id
    elif message.document:
        proof_type, proof_value = "document", message.document.file_id
    await db.execute("UPDATE orders SET status='paid', proof_type=?, proof_value=? WHERE id=?", (proof_type, proof_value, order_id))
    order = await db.one("SELECT o.*,p.name FROM orders o JOIN products p ON p.id=o.product_id WHERE o.id=?", (order_id,))
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Подтвердить", "ok", callback_data=f"approve:{order_id}"), btn("Отклонить", "cancel", callback_data=f"reject:{order_id}")]
    ])
    text = (
        f"<b>{ce('bell')} Новый оплаченный заказ №{order_id}</b>\n\n"
        f"Клиент: <a href=\"tg://user?id={message.from_user.id}\">{escape(message.from_user.full_name)}</a>\n"
        f"Товар: {escape(order['name'])}\nСумма: {order['price']} ₽"
    )
    for admin_id in ADMIN_IDS:
        try:
            if proof_type == "photo":
                await bot.send_photo(admin_id, proof_value, caption=text, reply_markup=admin_kb)
            elif proof_type == "document":
                await bot.send_document(admin_id, proof_value, caption=text, reply_markup=admin_kb)
            else:
                await bot.send_message(admin_id, text + f"\nПодтверждение: {escape(proof_value)}", reply_markup=admin_kb)
        except TelegramForbiddenError:
            pass
    await state.clear()
    await message.answer(f"<b>{ce('ok')} Оплата отправлена на проверку</b>\n\nМы уведомим вас после проверки.", reply_markup=home_kb(message.from_user.id))


@router.callback_query(F.data == "profile")
async def profile(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    count = (await db.one("SELECT COUNT(*) n FROM orders WHERE user_id=?", (callback.from_user.id,)))["n"]
    user = await db.one("SELECT * FROM users WHERE id=?", (callback.from_user.id,))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Пополнить баланс", "coin", callback_data="topup")],
        [btn("Мои заказы", "file", callback_data="orders")],
        [btn("Назад", "down", callback_data="home")],
    ])
    await edit_or_send(
        callback,
        f"<b>{ce('profile')} Профиль</b>\n\n"
        f"Имя: <b>{escape(callback.from_user.full_name)}</b>\n"
        f"ID: <code>{callback.from_user.id}</code>\n"
        f"Баланс: <b>{user['balance']} ₽</b>\nЗаказов: <b>{count}</b>",
        kb,
    )


@router.callback_query(F.data == "topup")
async def topup_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Flow.topup_amount)
    await edit_or_send(
        callback,
        f"<b>{ce('coin')} Пополнение баланса</b>\n\nВведите сумму пополнения в рублях, например: <code>1000</code>",
        back("profile"),
    )


@router.message(Flow.topup_amount)
async def topup_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(" ", "")
    if not raw.isdigit() or not 50 <= int(raw) <= 500_000:
        await message.answer("Введите сумму от 50 до 500 000 ₽ целым числом.")
        return
    amount = int(raw)
    topup_id = await db.execute("INSERT INTO topups(user_id,amount) VALUES(?,?)", (message.from_user.id, amount))
    await state.set_state(Flow.topup_proof)
    await state.update_data(topup_id=topup_id, amount=amount)
    await message.answer(
        f"<b>{ce('wallet')} Пополнение №{topup_id}</b>\n\n"
        f"Сумма: <b>{amount} ₽</b>\n\nРеквизиты:\n<code>{escape(PAYMENT_DETAILS)}</code>\n\n"
        f"{ce('attach')} После оплаты отправьте чек: фото, документ или текст.",
        reply_markup=back("profile"),
    )


@router.message(Flow.topup_proof)
async def topup_proof(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data(); topup_id = data["topup_id"]; amount = data["amount"]
    proof_type, proof_value = "text", message.text or message.caption or "Подтверждение"
    if message.photo:
        proof_type, proof_value = "photo", message.photo[-1].file_id
    elif message.document:
        proof_type, proof_value = "document", message.document.file_id
    await db.execute("UPDATE topups SET status='paid', proof_type=?, proof_value=? WHERE id=?", (proof_type, proof_value, topup_id))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Зачислить", "ok", callback_data=f"topup_ok:{topup_id}"), btn("Отклонить", "cancel", callback_data=f"topup_no:{topup_id}")]
    ])
    caption = (
        f"<b>{ce('coin')} Пополнение №{topup_id}</b>\n\n"
        f"Клиент: <a href=\"tg://user?id={message.from_user.id}\">{escape(message.from_user.full_name)}</a>\n"
        f"Сумма: <b>{amount} ₽</b>"
    )
    for admin_id in ADMIN_IDS:
        try:
            if proof_type == "photo": await bot.send_photo(admin_id, proof_value, caption=caption, reply_markup=kb)
            elif proof_type == "document": await bot.send_document(admin_id, proof_value, caption=caption, reply_markup=kb)
            else: await bot.send_message(admin_id, caption + f"\nПодтверждение: {escape(proof_value)}", reply_markup=kb)
        except TelegramForbiddenError:
            pass
    await state.clear()
    await message.answer(f"<b>{ce('clock')} Пополнение отправлено на проверку</b>", reply_markup=home_kb(message.from_user.id))


@router.callback_query(F.data.startswith("topup_ok:"))
async def topup_approve(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id): return
    topup_id = int(callback.data.split(":")[1])
    ok, user_id, amount = await db.approve_topup(topup_id)
    if not ok:
        await callback.answer("Уже обработано", show_alert=True); return
    await bot.send_message(user_id, f"<b>{ce('ok')} Баланс пополнен на {amount} ₽</b>")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Средства зачислены")


@router.callback_query(F.data.startswith("topup_no:"))
async def topup_reject(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id): return
    topup_id = int(callback.data.split(":")[1])
    topup = await db.one("SELECT * FROM topups WHERE id=?", (topup_id,))
    if not topup or topup["status"] != "paid":
        await callback.answer("Уже обработано", show_alert=True); return
    await db.execute("UPDATE topups SET status='rejected' WHERE id=?", (topup_id,))
    await bot.send_message(topup["user_id"], f"<b>{ce('cancel')} Пополнение №{topup_id} отклонено</b>\n\nСвяжитесь с поддержкой: {escape(SUPPORT)}")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Отклонено")


@router.callback_query(F.data == "orders")
async def orders(callback: CallbackQuery) -> None:
    items = await db.all("SELECT o.*,p.name FROM orders o JOIN products p ON p.id=o.product_id WHERE user_id=? ORDER BY o.id DESC LIMIT 10", (callback.from_user.id,))
    labels = {"new": "создан", "paid": "проверяется", "approved": "подтверждён", "done": "выполнен", "rejected": "отклонён", "cancelled": "отменён"}
    body = "\n\n".join(f"<b>№{x['id']} · {escape(x['name'])}</b>\n{x['price']} ₽ · {labels.get(x['status'], x['status'])}" for x in items) or "Заказов пока нет."
    await edit_or_send(callback, f"<b>{ce('file')} Мои заказы</b>\n\n{body}", back())


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@router.callback_query(F.data == "admin")
async def admin(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Заказы", "file", callback_data="admin_orders"), btn("Товары", "box", callback_data="admin_products")],
        [btn("Добавить товар", "edit", callback_data="admin_add"), btn("Рассылка", "broadcast", callback_data="admin_broadcast")],
        [btn("Статистика", "stats", callback_data="admin_stats")],
        [btn("Назад", "down", callback_data="home")],
    ])
    await edit_or_send(callback, f"<b>{ce('settings')} Админ-панель</b>\n\nВыберите действие:", kb)


@router.callback_query(F.data == "admin_orders")
async def admin_orders(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return
    items = await db.all("SELECT o.*,p.name,u.full_name FROM orders o JOIN products p ON p.id=o.product_id JOIN users u ON u.id=o.user_id WHERE o.status IN ('paid','approved') ORDER BY o.id DESC LIMIT 20")
    rows = [[btn(f"№{x['id']} · {x['name']} · {x['status']}", "file", callback_data=f"admin_order:{x['id']}")] for x in items]
    rows.append([btn("Назад", "down", callback_data="admin")])
    await edit_or_send(callback, f"<b>{ce('file')} Активные заказы</b>", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("admin_order:"))
async def admin_order(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return
    oid = int(callback.data.split(":")[1])
    o = await db.one("SELECT o.*,p.name,u.full_name FROM orders o JOIN products p ON p.id=o.product_id JOIN users u ON u.id=o.user_id WHERE o.id=?", (oid,))
    if not o: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Подтвердить", "ok", callback_data=f"approve:{oid}"), btn("Отклонить", "cancel", callback_data=f"reject:{oid}")],
        [btn("Назад", "down", callback_data="admin_orders")],
    ])
    await edit_or_send(callback, f"<b>Заказ №{oid}</b>\n{escape(o['name'])}\n{escape(o['full_name'])}\n{o['price']} ₽\nСтатус: {o['status']}", kb)


@router.callback_query(F.data.startswith("approve:"))
async def approve(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not is_admin(callback.from_user.id): return
    oid = int(callback.data.split(":")[1])
    o = await db.one("SELECT * FROM orders WHERE id=?", (oid,))
    if not o or o["status"] not in ("paid", "approved"):
        await callback.answer("Заказ уже обработан", show_alert=True); return
    await db.execute("UPDATE orders SET status='approved' WHERE id=?", (oid,))
    await bot.send_message(o["user_id"], f"<b>{ce('ok')} Оплата заказа №{oid} подтверждена</b>\n\nПодписка готовится к выдаче.")
    await state.set_state(Flow.delivery)
    await state.update_data(order_id=oid)
    await callback.message.answer(f"{ce('send')} Отправьте текст с данными подписки для клиента заказа №{oid}.", reply_markup=back("admin"))
    await callback.answer("Подтверждено")


@router.message(Flow.delivery)
async def delivery(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id): return
    data = await state.get_data(); oid = data["order_id"]
    o = await db.one("SELECT * FROM orders WHERE id=?", (oid,))
    await bot.send_message(o["user_id"], f"<b>{ce('gift')} Заказ №{oid} выполнен</b>\n\n{escape(message.text or '')}")
    await db.execute("UPDATE orders SET status='done' WHERE id=?", (oid,))
    await state.clear(); await message.answer("Данные отправлены клиенту.")


@router.callback_query(F.data.startswith("reject:"))
async def reject(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id): return
    oid = int(callback.data.split(":")[1]); o = await db.one("SELECT * FROM orders WHERE id=?", (oid,))
    if not o: return
    await db.execute("UPDATE orders SET status='rejected' WHERE id=?", (oid,))
    await bot.send_message(o["user_id"], f"<b>{ce('cancel')} Заказ №{oid} отклонён</b>\n\nСвяжитесь с поддержкой: {escape(SUPPORT)}")
    await callback.answer("Отклонено"); await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data == "admin_products")
async def admin_products(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return
    items = await db.all("SELECT * FROM products ORDER BY id")
    rows = [[btn(f"{p['name']} · {p['price']} ₽ · {p['stock']} шт.", "eye" if p['active'] else "hidden", callback_data=f"admin_product:{p['id']}")] for p in items]
    rows.append([btn("Назад", "down", callback_data="admin")])
    await edit_or_send(callback, f"<b>{ce('box')} Товары</b>\n\nНажмите, чтобы включить или выключить:", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("admin_product:"))
async def admin_product(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return
    pid = int(callback.data.split(":")[1])
    p = await db.one("SELECT * FROM products WHERE id=?", (pid,))
    if not p: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("Поступление товара", "box", callback_data=f"stock:{pid}"), btn("Изменить цену", "coin", callback_data=f"price:{pid}")],
        [btn("Выключить" if p["active"] else "Включить", "eye" if p["active"] else "hidden", callback_data=f"toggle:{pid}")],
        [btn("Перейти к товару", "link", url=f"https://t.me/{BOT_USERNAME}?start=product_{pid}")],
        [btn("Назад", "down", callback_data="admin_products")],
    ])
    await edit_or_send(
        callback,
        f"<b>{ce('tag')} {escape(p['name'])}</b>\n\nЦена: <b>{p['price']} ₽</b>\n"
        f"Остаток: <b>{p['stock']} шт.</b>\nСтатус: <b>{'в продаже' if p['active'] else 'скрыт'}</b>",
        kb,
    )


@router.callback_query(F.data.startswith("toggle:"))
async def toggle(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return
    pid = int(callback.data.split(":")[1])
    await db.execute("UPDATE products SET active=1-active WHERE id=?", (pid,))
    await admin_products(callback)


@router.callback_query(F.data.startswith("stock:"))
async def stock_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id): return
    pid = int(callback.data.split(":")[1])
    await state.set_state(Flow.product_stock); await state.update_data(product_id=pid)
    await edit_or_send(callback, f"<b>{ce('box')} Поступление товара</b>\n\nВведите количество новых единиц, например: <code>10</code>", back(f"admin_product:{pid}"))


@router.message(Flow.product_stock)
async def stock_save(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 100_000:
        await message.answer("Введите количество от 1 до 100 000."); return
    data = await state.get_data(); pid = data["product_id"]
    await db.execute("UPDATE products SET stock=stock+? WHERE id=?", (int(raw), pid))
    await state.clear(); await message.answer(f"{ce('ok')} Добавлено: {int(raw)} шт.")


@router.callback_query(F.data.startswith("price:"))
async def price_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id): return
    pid = int(callback.data.split(":")[1])
    await state.set_state(Flow.product_price); await state.update_data(product_id=pid)
    await edit_or_send(callback, f"<b>{ce('coin')} Изменение цены</b>\n\nВведите новую цену в рублях:", back(f"admin_product:{pid}"))


@router.message(Flow.product_price)
async def price_save(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    raw = (message.text or "").replace(" ", "")
    if not raw.isdigit() or not 1 <= int(raw) <= 10_000_000:
        await message.answer("Введите корректную цену целым числом."); return
    data = await state.get_data(); pid = data["product_id"]
    await db.execute("UPDATE products SET price=? WHERE id=?", (int(raw), pid))
    await state.clear(); await message.answer(f"{ce('ok')} Новая цена: {int(raw)} ₽")


@router.callback_query(F.data == "admin_add")
async def admin_add(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id): return
    await state.set_state(Flow.product)
    await edit_or_send(callback, f"<b>{ce('edit')} Новый товар</b>\n\nОтправьте одной строкой:\n<code>Название | Описание | Цена | Количество</code>", back("admin"))


@router.message(Flow.product)
async def add_product(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    try:
        name, description, raw_price, raw_stock = [x.strip() for x in (message.text or "").split("|", 3)]
        price, stock = int(raw_price), int(raw_stock)
        if not name or not description or price <= 0 or stock < 0: raise ValueError
    except ValueError:
        await message.answer("Неверный формат. Пример: ChatGPT Plus | На 1 месяц | 1990 | 10"); return
    await db.execute("INSERT INTO products(name,description,price,stock) VALUES(?,?,?,?)", (name, description, price, stock))
    await state.clear(); await message.answer(f"{ce('ok')} Товар добавлен.")


@router.callback_query(F.data == "admin_stats")
async def stats(callback: CallbackQuery) -> None:
    if not is_admin(callback.from_user.id): return
    users = (await db.one("SELECT COUNT(*) n FROM users"))["n"]
    orders_n = (await db.one("SELECT COUNT(*) n FROM orders"))["n"]
    revenue = (await db.one("SELECT COALESCE(SUM(price),0) n FROM orders WHERE status='done'"))["n"]
    await edit_or_send(callback, f"<b>{ce('stats')} Статистика</b>\n\nПользователей: <b>{users}</b>\nЗаказов: <b>{orders_n}</b>\nВыручка: <b>{revenue} ₽</b>", back("admin"))


@router.callback_query(F.data == "admin_broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not is_admin(callback.from_user.id): return
    await state.set_state(Flow.broadcast)
    await edit_or_send(callback, f"<b>{ce('broadcast')} Отправьте сообщение для рассылки всем пользователям.</b>", back("admin"))


@router.message(Flow.broadcast)
async def broadcast(message: Message, state: FSMContext, bot: Bot) -> None:
    if not is_admin(message.from_user.id): return
    users = await db.all("SELECT id FROM users"); sent = 0
    for user in users:
        try:
            await bot.copy_message(user["id"], message.chat.id, message.message_id); sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.04)
    await state.clear(); await message.answer(f"{ce('ok')} Рассылка завершена. Доставлено: {sent}/{len(users)}")


@router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id): return
    fake = InlineKeyboardMarkup(inline_keyboard=[[btn("Открыть админ-панель", "settings", callback_data="admin")]])
    await message.answer(f"<b>{ce('settings')} Управление магазином</b>", reply_markup=fake)


async def main() -> None:
    if not TOKEN or not ADMIN_IDS:
        raise RuntimeError("Заполните BOT_TOKEN и ADMIN_IDS в файле .env")
    logging.basicConfig(level=logging.INFO)
    await db.init()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(); dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
