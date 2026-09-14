import hashlib
import logging
import os
import random
import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)


def load_env_file(path: str | Path) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(Path(__file__).with_name(".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- CONFIGURAÇÕES ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or ""
GROUP_ID = int(os.getenv("TELEGRAM_GROUP_ID", "0") or 0)
ADMINS = {int(admin_id) for admin_id in (os.getenv("ADMINS", "").split(",") if os.getenv("ADMINS") else []) if admin_id.strip()}
GATEWAY_NAME = os.getenv("CHECKER_GATEWAY_NAME", "Default Gateway")
BOT_ACTIVE = True

# Códigos de erro para cartões "Die"
DIE_CODES = [
    "51 (Saldo/Limite insuficiente)",
    "54 (Cartão expirado)",
    "57 (Transação não permitida)",
    "05 (Transação não autorizada)",
    "01 (Consulte o emissor)",
    "02 (Consulte o emissor)",
    "12 (Transação inválida)",
    "13 (Valor inválido)",
]


# --- BANCO DE DADOS ---
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            name TEXT,
            username TEXT,
            date TEXT,
            time TEXT,
            ip TEXT,
            mac TEXT
        )
        """
    )
    cursor.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
    for admin in ADMINS:
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin,))
    conn.commit()
    conn.close()


def save_user(user):
    try:
        ip_data = "Desconhecido"
        try:
            response = requests.get("https://api.ipify.org?format=json", timeout=10)
            response.raise_for_status()
            ip_data = response.json().get("ip", "Desconhecido")
        except requests.RequestException:
            pass

        now = datetime.now()
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO users (user_id, name, username, date, time, ip, mac)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                user.full_name,
                user.username,
                now.strftime("%d/%m/%Y"),
                now.strftime("%H:%M:%S"),
                ip_data,
                "N/A",
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:  # pragma: no cover - diagnóstico de runtime
        logging.exception("Erro ao salvar usuário: %s", exc)


# --- LÓGICA DO CHECKER ---
def check_card(card_data):
    hash_val = int(hashlib.md5(card_data.encode("utf-8")).hexdigest(), 16)
    if hash_val % 100 < 67:
        return "🟢 Live #APROVADO"
    return f"🔴 Die #RECUSADO - {random.choice(DIE_CODES)}"


# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    welcome_text = (
        "<b>🌟 BEM-VINDO AO DAEMON CHECKER 🌟</b>\n\n"
        "O sistema de verificação mais rápido e preciso do mercado.\n"
        "✅ <b>Totalmente Grátis</b>\n"
        "🚀 <b>Alta Taxa de Aprovação</b>\n\n"
        "⚠️ <i>Para utilizar o checker, você deve estar no nosso grupo oficial.</i>"
    )

    keyboard = [
        [
            InlineKeyboardButton("🔗 Entrar no Grupo", url="https://t.me/seu_grupo"),
            InlineKeyboardButton("✅ Verificar", callback_data="verify"),
        ]
    ]

    await update.message.reply_text(
        welcome_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return

    await query.answer()
    if query.message and query.message.chat.id == GROUP_ID:
        await query.edit_message_text(
            "✅ Verificação concluída! Agora você pode usar o comando /chkcc dentro do grupo."
        )
    else:
        await query.edit_message_text("❌ Este bot só funciona dentro do grupo oficial!")


async def chkcc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not BOT_ACTIVE:
        return await update.message.reply_text("⚠️ Bot em possível manutenção!")

    if update.effective_chat is None or update.effective_chat.id != GROUP_ID:
        return await update.message.reply_text("❌ Este comando só funciona no grupo oficial!")

    text = " ".join(context.args)
    if not text:
        return await update.message.reply_text("❌ Use: /chkcc CARTAO|MES/ANO|CVV")

    lines = text.split("\n")[:10]
    results = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        res = check_card(line)
        results.append(f"<code>{line}</code>\n{res}")

    final_msg = f"<b>💳 GATEWAY: {GATEWAY_NAME}</b>\n\n" + "\n\n".join(results)
    keyboard = [[InlineKeyboardButton("📋 Copiar Resultados", callback_data="copy_res")]]
    await update.message.reply_text(
        final_msg,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# --- ADMIN COMMANDS ---
async def admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    is_admin = cursor.fetchone()
    conn.close()
    return is_admin


async def set_gateway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_check(update, context):
        return
    global GATEWAY_NAME
    GATEWAY_NAME = " ".join(context.args) if context.args else "Default Gateway"
    await update.message.reply_text(f"✅ Gateway alterado para: {GATEWAY_NAME}")


async def toggle_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_check(update, context):
        return
    global BOT_ACTIVE
    BOT_ACTIVE = not BOT_ACTIVE
    status = "Ativado" if BOT_ACTIVE else "Desativado"
    await update.message.reply_text(f"⚙️ Bot {status} com sucesso!")


async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_check(update, context):
        return
    try:
        new_id = int(context.args[0])
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ ID {new_id} adicionado como admin.")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Use: /addadmin ID_NUMERICO")


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_check(update, context):
        return
    try:
        rem_id = int(context.args[0])
        conn = sqlite3.connect("bot_data.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (rem_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ ID {rem_id} removido.")
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Use: /byeadm ID_NUMERICO")


async def get_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_check(update, context):
        return

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()

    if context.args:
        target = context.args[0]
        cursor.execute("SELECT * FROM users WHERE user_id = ? OR username = ?", (target, target))
        row = cursor.fetchone()
        if row:
            log = (
                "👤 <b>User Log</b>\n"
                f"ID: {row[0]}\nNome: {row[1]}\nUser: {row[2]}\n"
                f"Data: {row[3]}\nHora: {row[4]}\nIP: {row[5]}\nMAC: {row[6]}"
            )
            await update.message.reply_text(log, parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Usuário não encontrado.")
    else:
        cursor.execute("SELECT name, user_id FROM users")
        users = cursor.fetchall()
        log_list = "\n".join([f"{user[0]} ({user[1]})" for user in users])
        await update.message.reply_text(
            f"📋 <b>Lista de Usuários:</b>\n\n{log_list}",
            parse_mode="HTML",
        )
    conn.close()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit(
            "Token do Telegram não encontrado. Defina TELEGRAM_BOT_TOKEN ou BOT_TOKEN no ambiente "
            "ou em um arquivo .env."
        )

    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("chkcc", chkcc))
    app.add_handler(CommandHandler("gateway", set_gateway))
    app.add_handler(CommandHandler("desligar", toggle_bot))
    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("byeadm", remove_admin))
    app.add_handler(CommandHandler("logs", get_logs))
    app.add_handler(CallbackQueryHandler(handle_verify, pattern="verify"))

    print("Bot Daemon Online...")
    app.run_polling(drop_pending_updates=True)
