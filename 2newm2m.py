"""
TeleFleet Single-File Telegram Bot (bot.py)
-----------------------------------------
Features:
  1. /add         -> Phone -> OTP (supports 2FA) -> adds account (max 20)
  2. /names       -> Send X names -> randomly renames X accounts
  3. /dp          -> Send K photos -> updates K accounts' DP (auto-deletes old DPs, no photos saved) & resets rest to Default DP
  4. /join        -> Send link -> send X count -> X accounts join or send join request
  5. /leave       -> Click any joined channel name button (or send link) -> choose count & delay -> accounts leave
  6. /channels    -> Scan & list all joined channels with 1-tap Leave buttons
  7. /approve     -> Owner approves any member by Telegram Chat/User ID (/unapprove, /users)
  8. /list        -> Check all added accounts, tap an account to inspect & leave its channels
  9. /remove      -> Remove an account by slot number or phone
 10. /cancel      -> Cancel active workflow step
  + Stylish small-caps typography, HTML card formatting, inline menu buttons, and live progress animations.

Setup:
  pip install telethon
  export BOT_TOKEN="your_botfather_token"
  export API_ID="1234567"
  export API_HASH="your_api_hash_from_my_telegram_org"
  python3 bot.py
"""

import asyncio
import html
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    from telethon import Button, TelegramClient, events
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "telethon"])
    from telethon import Button, TelegramClient, events
from telethon.errors import (
    ChannelPrivateError,
    FloodWaitError,
    InviteRequestSentError,
    MessageNotModifiedError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    RPCError,
    SessionPasswordNeededError,
    UserAlreadyParticipantError,
    UserNotParticipantError,
)
try:
    from telethon.network import ConnectionTcpAbridged
except ImportError:
    ConnectionTcpAbridged = None
from telethon.sessions import StringSession
from telethon.tl.functions.account import UpdateProfileRequest
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import (
    CheckChatInviteRequest,
    DeleteChatUserRequest,
    ImportChatInviteRequest,
)
from telethon.tl.functions.photos import DeletePhotosRequest, UploadProfilePhotoRequest
from telethon.tl.types import Channel, Chat, ChatInviteAlready

# ---------------------------------------------------------------------------
# Configuration (set via environment variables or replace defaults below)
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "9005455767:AAFyKzYJ98ocl3v90bHs7vtmZ8GYvUBXCi0").strip()
API_ID = int(os.getenv("API_ID", "31866718") or "0")
API_HASH = os.getenv("API_HASH", "4e657aae0f6ca48102999c18404be290").strip()
# Optional: restrict owner to your Telegram numeric user ID (0 = lock to first /start user)
OWNER_ID = int(os.getenv("OWNER_ID", "7536482921") or "8116350168")

MAX_ACCOUNTS = 20
STORE_FILE = Path(__file__).resolve().parent / "accounts.json"


# ---------------------------------------------------------------------------
# Premium Stylish Font, Card & Animation Helpers
# ---------------------------------------------------------------------------
_SMALL_CAPS_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
)

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
PULSE_FRAMES = ["◇", "◈", "✦", "◈"]


def stylish(text: str) -> str:
    """Convert ASCII letters into clean Unicode Small Caps for premium headers."""
    return text.translate(_SMALL_CAPS_MAP)


def esc(text: Any) -> str:
    return html.escape(str(text))


def render_progress_bar(done: int, total: int, width: int = 12) -> str:
    if total <= 0:
        return "▱" * width + " 0%"
    ratio = max(0.0, min(1.0, done / total))
    filled = int(round(ratio * width))
    bar = "▰" * filled + "▱" * (width - filled)
    pct = int(round(ratio * 100))
    return f"{bar} {pct}%"


def format_card(title: str, lines: list[str], footer: str | None = None, icon: str = "✦") -> str:
    body = "\n".join(lines)
    card = f"<b>{icon} {esc(stylish(title))}</b>\n<blockquote>{body}</blockquote>"
    if footer:
        card += f"\n<i>{esc(footer)}</i>"
    return card


async def safe_edit(msg: Any, text: str, buttons: Any = None) -> None:
    try:
        await msg.edit(text, parse_mode="html", buttons=buttons)
    except MessageNotModifiedError:
        pass
    except Exception:
        pass


def create_client(session_obj: Any, use_abridged: bool = False) -> TelegramClient:
    """Create a resilient TelegramClient with higher retries & cloud-friendly timeouts."""
    kwargs: dict[str, Any] = {
        "request_retries": 10,
        "connection_retries": 10,
        "retry_delay": 2,
        "timeout": 20,
        "auto_reconnect": True,
        "use_ipv6": False,
    }
    if use_abridged and ConnectionTcpAbridged is not None:
        kwargs["connection"] = ConnectionTcpAbridged
    try:
        return TelegramClient(session_obj, API_ID, API_HASH, **kwargs)
    except TypeError:
        return TelegramClient(session_obj, API_ID, API_HASH)


async def safe_disconnect(client: Any) -> None:
    if client is None:
        return
    try:
        await client.disconnect()
    except Exception:
        pass


async def ensure_connected(client: TelegramClient) -> None:
    try:
        if not client.is_connected():
            await client.connect()
    except Exception:
        await client.connect()


def _is_transient_telethon_error(exc: BaseException) -> bool:
    if isinstance(exc, RPCError):
        return False
    if isinstance(exc, (ConnectionError, TimeoutError, OSError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, ValueError) and "unsuccessful" in str(exc).lower():
        return True
    return False


async def run_with_retry(
    op_factory: Any,
    retries: int = 3,
    delay: float = 1.8,
    client: TelegramClient | None = None,
) -> Any:
    last_exc: BaseException | None = None
    for attempt in range(1, retries + 1):
        try:
            if client is not None:
                await ensure_connected(client)
            return await op_factory()
        except Exception as exc:
            last_exc = exc
            if not _is_transient_telethon_error(exc) or attempt >= retries:
                raise
            if client is not None:
                await safe_disconnect(client)
            await asyncio.sleep(delay * attempt)
            if client is not None:
                await ensure_connected(client)
    if last_exc is not None:
        raise last_exc



async def play_intro_animation(event: events.NewMessage.Event, account_count: int, is_owner: bool) -> Any:
    """Smooth 4-frame boot animation editing seamlessly into the styled Command Center."""
    frames = [
        ("⠋ Booting TeleFleet Pro...", 1, 4),
        ("⠹ Loading encrypted sessions...", 2, 4),
        (f"⠼ Syncing fleet ({account_count}/{MAX_ACCOUNTS} slots)...", 3, 4),
        ("✔ Workspace ready!", 4, 4),
    ]
    label, step, total = frames[0]
    msg = await event.reply(
        format_card(
            "TeleFleet Pro",
            [f"<code>{label}</code>", f"<code>{render_progress_bar(step, total, 10)}</code>"],
            icon="◈",
        ),
        parse_mode="html",
    )
    for label, step, total in frames[1:]:
        await asyncio.sleep(0.26)
        await safe_edit(
            msg,
            format_card(
                "TeleFleet Pro",
                [f"<code>{label}</code>", f"<code>{render_progress_bar(step, total, 10)}</code>"],
                icon="◈",
            ),
        )
    await asyncio.sleep(0.22)
    return msg


# ---------------------------------------------------------------------------
# Persistence & Parsing Helpers
# ---------------------------------------------------------------------------
def load_store() -> dict[str, Any]:
    if not STORE_FILE.exists():
        return {"owner_id": OWNER_ID, "approved_ids": [], "delay_seconds": 1.0, "accounts": []}
    try:
        data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"owner_id": OWNER_ID, "approved_ids": [], "delay_seconds": 1.0, "accounts": []}
        data.setdefault("owner_id", OWNER_ID)
        data.setdefault("approved_ids", [])
        data.setdefault("delay_seconds", 1.0)
        data.setdefault("accounts", [])
        return data
    except Exception:
        return {"owner_id": OWNER_ID, "approved_ids": [], "delay_seconds": 1.0, "accounts": []}


def save_store(data: dict[str, Any]) -> None:
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(STORE_FILE)


def normalize_phone(raw: str) -> str | None:
    cleaned = re.sub(r"[\s\-().]", "", raw.strip())
    digits = cleaned[1:] if cleaned.startswith("+") else cleaned
    if not digits.isdigit() or not (7 <= len(digits) <= 15):
        return None
    return f"+{digits}"


def parse_names(raw: str) -> list[str]:
    parts = re.split(r"[\r\n,]+", raw)
    return [p.strip() for p in parts if p.strip()]


def parse_delay_seconds(raw: str) -> float | None:
    """
    Parse delay like: '0', '5', '2.5', '10s', '1.5m', '1m30s', '1h'.
    Capped between 0 and 3600 seconds (1 hour).
    """
    text = raw.strip().lower().replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        val = float(text)
        return val if 0.0 <= val <= 3600.0 else None

    m_combo = re.fullmatch(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?:in)?)?(?:(\d+(?:\.\d+)?)s(?:ec)?)?", text)
    if m_combo and any(m_combo.groups()):
        hours = float(m_combo.group(1) or 0)
        mins = float(m_combo.group(2) or 0)
        secs = float(m_combo.group(3) or 0)
        val = hours * 3600.0 + mins * 60.0 + secs
        return round(val, 2) if 0.0 <= val <= 3600.0 else None
    return None


def format_delay(seconds: float) -> str:
    if seconds <= 0:
        return "0s (instant)"
    if seconds < 60:
        return f"{int(seconds)}s" if float(seconds).is_integer() else f"{seconds:.1f}s"
    mins, rem = divmod(int(round(seconds)), 60)
    if rem == 0:
        return f"{mins}m"
    return f"{mins}m {rem}s"


def parse_join_target(raw: str) -> tuple[str, str] | None:
    """
    Returns ('invite', invite_hash), ('username', username), or ('chat_id', id_str).
    Supports:
      - https://t.me/+AbCdEfG
      - https://t.me/joinchat/AbCdEfG
      - tg://join?invite=AbCdEfG
      - https://t.me/channel_name
      - @channel_name
      - -1001234567890 (numeric chat ID)
    """
    text = raw.strip()
    if not text:
        return None

    if re.match(r"^-?\d{5,}$", text):
        return ("chat_id", text)

    if text.startswith("@"):
        uname = text[1:].split("/")[0].strip()
        return ("username", uname) if re.match(r"^[A-Za-z0-9_]{3,}$", uname) else None

    if text.startswith("tg://"):
        parsed = urlparse(text)
        if parsed.netloc == "join":
            invite = parse_qs(parsed.query).get("invite", [""])[0].strip()
            return ("invite", invite) if invite else None
        if parsed.netloc == "resolve":
            domain = parse_qs(parsed.query).get("domain", [""])[0].strip()
            return ("username", domain) if re.match(r"^[A-Za-z0-9_]{3,}$", domain) else None
        return None

    if not re.match(r"^https?://", text, re.IGNORECASE):
        if re.match(r"^(t\.me|telegram\.me)/", text, re.IGNORECASE):
            text = "https://" + text
        elif re.match(r"^[A-Za-z0-9_]{4,}$", text):
            return ("username", text)
        else:
            return None

    parsed = urlparse(text)
    host = parsed.netloc.lower()
    if host not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me", "telegram.dog"}:
        return None

    path = parsed.path.strip("/")
    if not path:
        return None

    if path.startswith("+"):
        invite_hash = path[1:].split("/")[0].strip()
        return ("invite", invite_hash) if invite_hash else None

    if path.startswith("joinchat/"):
        invite_hash = path.split("/", 1)[1].split("/")[0].strip()
        return ("invite", invite_hash) if invite_hash else None

    username = path.split("/")[0].strip()
    if re.match(r"^[A-Za-z0-9_]{3,}$", username):
        return ("username", username)
    return None


def _channel_key(cid: Any = None, username: str | None = None, target: str | None = None) -> str:
    if cid is not None and str(cid).strip():
        return f"id:{str(cid).strip()}"
    if username and username.strip():
        return f"u:{username.strip().lstrip('@').lower()}"
    if target and target.strip():
        return f"t:{target.strip().lower()}"
    return "unknown"


def upsert_account_channel(
    acc: dict[str, Any],
    cid: int | None = None,
    title: str | None = None,
    username: str | None = None,
    target: str | None = None,
) -> None:
    channels: list[dict[str, Any]] = list(acc.get("channels") or [])
    clean_uname = username.strip().lstrip("@") if username else None
    clean_title = (title or (f"@{clean_uname}" if clean_uname else target) or "Channel").strip()
    clean_target = (target or (f"@{clean_uname}" if clean_uname else (str(cid) if cid is not None else clean_title))).strip()
    new_key = _channel_key(cid, clean_uname, clean_target)

    updated: list[dict[str, Any]] = []
    matched = False
    for ch in channels:
        ch_key = _channel_key(ch.get("id"), ch.get("username"), ch.get("target"))
        same_uname = bool(clean_uname and ch.get("username") and str(ch.get("username")).lstrip("@").lower() == clean_uname.lower())
        same_target = bool(clean_target and ch.get("target") and str(ch.get("target")).lower() == clean_target.lower())
        if ch_key == new_key or same_uname or same_target:
            updated.append(
                {
                    "id": cid if cid is not None else ch.get("id"),
                    "title": clean_title or ch.get("title") or clean_target,
                    "username": clean_uname or ch.get("username"),
                    "target": clean_target or ch.get("target"),
                }
            )
            matched = True
        else:
            updated.append(ch)
    if not matched:
        updated.append(
            {
                "id": cid,
                "title": clean_title,
                "username": clean_uname,
                "target": clean_target,
            }
        )
    acc["channels"] = updated


def remove_account_channel(
    acc: dict[str, Any],
    leave_type: str,
    leave_value: str,
    leave_raw: str = "",
) -> None:
    channels: list[dict[str, Any]] = list(acc.get("channels") or [])
    if not channels:
        return
    lv = leave_value.strip().lstrip("@").lower()
    raw_norm = leave_raw.strip().lower()
    kept: list[dict[str, Any]] = []
    for ch in channels:
        ch_id = str(ch.get("id") or "").strip()
        ch_uname = str(ch.get("username") or "").strip().lstrip("@").lower()
        ch_target = str(ch.get("target") or "").strip().lower()
        ch_title = str(ch.get("title") or "").strip().lower()
        is_match = False
        if leave_type == "chat_id" and ch_id == leave_value.strip():
            is_match = True
        elif leave_type == "username" and (ch_uname == lv or ch_target == f"@{lv}" or ch_target == lv):
            is_match = True
        elif leave_type == "invite" and (leave_value.lower() in ch_target or (raw_norm and ch_target == raw_norm)):
            is_match = True
        elif raw_norm and (ch_title == raw_norm or ch_target == raw_norm):
            is_match = True
        if not is_match:
            kept.append(ch)
    acc["channels"] = kept


def aggregate_fleet_channels(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for acc in accounts:
        phone = acc.get("phone", "")
        for ch in acc.get("channels", []) or []:
            cid = ch.get("id")
            uname = (str(ch.get("username")).lstrip("@") if ch.get("username") else None) or None
            target = str(ch.get("target") or (f"@{uname}" if uname else (str(cid) if cid is not None else ""))).strip()
            title = str(ch.get("title") or target or "Channel").strip()

            key = _channel_key(cid, uname, target)
            # Deduplicate by username if one entry lacked cid
            if key not in buckets and uname:
                for existing_k, existing_val in buckets.items():
                    if (existing_val.get("username") or "").lower() == uname.lower():
                        key = existing_k
                        break

            if key not in buckets:
                if cid is not None:
                    leave_type, leave_value = "chat_id", str(cid)
                elif uname:
                    leave_type, leave_value = "username", uname
                else:
                    parsed = parse_join_target(target)
                    if parsed:
                        leave_type, leave_value = parsed
                    else:
                        leave_type, leave_value = "username", target.lstrip("@")
                buckets[key] = {
                    "id": cid,
                    "title": title,
                    "username": uname,
                    "target": target,
                    "leave_type": leave_type,
                    "leave_value": leave_value,
                    "phones": [],
                }
            entry = buckets[key]
            if cid is not None and entry.get("id") is None:
                entry["id"] = cid
                entry["leave_type"] = "chat_id"
                entry["leave_value"] = str(cid)
            if uname and not entry.get("username"):
                entry["username"] = uname
            if title and (entry["title"].startswith("@") or entry["title"].startswith("http") or entry["title"].lstrip("-").isdigit()):
                entry["title"] = title
            if phone and phone not in entry["phones"]:
                entry["phones"].append(phone)

    return sorted(buckets.values(), key=lambda c: (-len(c["phones"]), c["title"].lower()))



# ---------------------------------------------------------------------------
# Authorization Helpers
# ---------------------------------------------------------------------------
def get_owner_id(store: dict[str, Any]) -> int:
    return int(store.get("owner_id") or OWNER_ID or 0)


def is_owner(sender_id: int, store: dict[str, Any]) -> bool:
    owner_id = get_owner_id(store)
    if owner_id == 0:
        store["owner_id"] = sender_id
        save_store(store)
        return True
    return sender_id == owner_id


def is_authorized(sender_id: int, store: dict[str, Any]) -> bool:
    if is_owner(sender_id, store):
        return True
    approved = [int(uid) for uid in store.get("approved_ids", [])]
    return sender_id in approved


async def deny_access(event: Any) -> None:
    uid = event.sender_id
    msg = format_card(
        "Access Restricted",
        [
            "You are not approved to use this bot yet.",
            f"Your Chat ID: <code>{uid}</code>",
            f"Ask the owner to run: <code>/approve {uid}</code>",
        ],
    )
    await event.reply(msg, parse_mode="html")


# ---------------------------------------------------------------------------
# Runtime State & Menu Builder
# ---------------------------------------------------------------------------
user_states: dict[int, dict[str, Any]] = {}
active_cancels: dict[int, asyncio.Event] = {}
channel_list_cache: dict[int, list[dict[str, Any]]] = {}


async def cleanup_state(chat_id: int) -> None:
    cancel_ev = active_cancels.get(chat_id)
    if cancel_ev is not None:
        cancel_ev.set()
    state = user_states.pop(chat_id, None)
    if not state:
        return
    client = state.get("client")
    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            pass
    for file_path in state.get("dp_files", []):
        try:
            os.remove(file_path)
        except OSError:
            pass


async def sleep_with_countdown(
    progress_msg: Any,
    title: str,
    done: int,
    total: int,
    delay_seconds: float,
    cancel_ev: asyncio.Event,
) -> bool:
    """
    Waits delay_seconds while animating a live countdown in the Telegram message.
    Returns True if cancelled mid-wait, False otherwise.
    """
    if delay_seconds <= 0:
        return cancel_ev.is_set()

    end_time = time.monotonic() + delay_seconds
    tick = 0
    while True:
        if cancel_ev.is_set():
            return True
        remaining = end_time - time.monotonic()
        if remaining <= 0:
            return False

        if delay_seconds >= 2.0 and (tick == 0 or tick % 2 == 0):
            spinner = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
            pulse = PULSE_FRAMES[tick % len(PULSE_FRAMES)]
            await safe_edit(
                progress_msg,
                format_card(
                    title,
                    [
                        f"<code>{spinner} Cooldown: {remaining:.0f}s before next account ({done}/{total})</code>",
                        f"<code>{render_progress_bar(done, total)}</code>",
                    ],
                    icon=pulse,
                ),
            )
        step = min(1.0, remaining)
        try:
            await asyncio.wait_for(cancel_ev.wait(), timeout=step)
            return True
        except asyncio.TimeoutError:
            pass
        tick += 1


def delay_preset_buttons(prefix: str, default_delay: float) -> list[list[Any]]:
    return [
        [Button.inline(f"⚡ Default ({format_delay(default_delay)})", f"{prefix}:{default_delay}".encode())],
        [
            Button.inline("0s", f"{prefix}:0".encode()),
            Button.inline("3s", f"{prefix}:3".encode()),
            Button.inline("5s", f"{prefix}:5".encode()),
            Button.inline("15s", f"{prefix}:15".encode()),
            Button.inline("30s", f"{prefix}:30".encode()),
        ],
    ]


def build_menu_payload(store: dict[str, Any], owner_view: bool) -> tuple[str, list[list[Any]]]:
    count = len(store["accounts"])
    approved_count = len(store.get("approved_ids", []))
    default_delay = float(store.get("delay_seconds", 1.0))
    lines = [
        f"• <b>{esc(stylish('Fleet Slots'))}:</b> <code>{count}/{MAX_ACCOUNTS}</code>",
        f"• <b>{esc(stylish('Approved Members'))}:</b> <code>{approved_count}</code>",
        f"• <b>{esc(stylish('Default Request Delay'))}:</b> <code>{esc(format_delay(default_delay))}</code>",
        "",
        "<code>/add</code>      Add Telegram account via OTP",
        "<code>/names</code>    Rename X random accounts",
        "<code>/dp</code>       Update DPs (auto-deletes old DPs)",
        "<code>/join</code>     Join or request link (with custom delay)",
        "<code>/leave</code>    Click a channel name button (or link) to leave",
        "<code>/channels</code> Scan &amp; manage joined channels",
        "<code>/delay</code>    Set default time gap per account",
        "<code>/list</code>     Check added accounts &amp; their channels",
        "<code>/remove</code>   Remove account by slot or phone",
    ]
    if owner_view:
        lines.extend(
            [
                "<code>/approve &lt;chat_id&gt;</code>    Approve member",
                "<code>/unapprove &lt;chat_id&gt;</code>  Revoke member",
                "<code>/users</code>               List approved IDs",
            ]
        )
    text = format_card("TeleFleet Command Center", lines, "Select an action below or send a command.")
    buttons = [
        [Button.inline("＋ Add Account", b"menu_add"), Button.inline("✎ Random Names", b"menu_names")],
        [Button.inline("◈ Batch DPs", b"menu_dp"), Button.inline("↗ Join Link", b"menu_join")],
        [Button.inline("↙ Leave Channel", b"menu_leave"), Button.inline("⏱ Set Delay", b"menu_delay")],
        [Button.inline("☰ Added Accounts", b"menu_list"), Button.inline("📢 Channels", b"menu_channels")],
        [Button.inline("↻ Refresh Menu", b"menu_refresh")],
    ]
    return text, buttons


# ---------------------------------------------------------------------------
# Core Batch Workers with Live Animated Progress
# ---------------------------------------------------------------------------
async def apply_dp_batch(
    event_chat_id: int,
    progress_msg: Any,
    dp_files: list[str],
    apply_single_to_all: bool = False,
) -> str:
    store = load_store()
    accounts: list[dict[str, Any]] = store["accounts"]
    if not accounts:
        return format_card("Error", ["No accounts added yet. Use <code>/add</code> first."])

    total = len(accounts)
    if not dp_files:
        return format_card("Error", ["No DP photos queued. Send <code>/dp</code> and attach photos first."])

    shuffled_indices = list(range(total))
    random.shuffle(shuffled_indices)

    changed_count = 0
    defaulted_count = 0
    errors: list[str] = []

    if len(dp_files) == 1 and apply_single_to_all:
        apply_count = total
    else:
        apply_count = min(len(dp_files), total)

    delay_seconds = float(store.get("delay_seconds", 1.0))
    cancel_ev = asyncio.Event()
    active_cancels[event_chat_id] = cancel_ev
    cancelled = False

    last_edit = 0.0
    try:
        for pos, acc_idx in enumerate(shuffled_indices):
            if cancel_ev.is_set():
                cancelled = True
                break
            acc = accounts[acc_idx]
            now = time.monotonic()
            if now - last_edit >= 0.7 or pos == 0:
                spinner = SPINNER_FRAMES[pos % len(SPINNER_FRAMES)]
                pulse = PULSE_FRAMES[pos % len(PULSE_FRAMES)]
                bar = render_progress_bar(pos, total)
                await safe_edit(
                    progress_msg,
                    format_card(
                        "Updating Profile Photos",
                        [
                            f"<code>{spinner} Processing {esc(acc['phone'])} ({pos + 1}/{total})</code>",
                            f"<code>{bar}</code>",
                        ],
                        icon=pulse,
                    ),
                )
                last_edit = now

            client = create_client(StringSession(acc["session"]))
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    errors.append(f"{acc['phone']}: session expired")
                    continue

                if pos < apply_count:
                    photo_path = dp_files[0] if (len(dp_files) == 1 and apply_single_to_all) else dp_files[pos]
                    old_photos = await client.get_profile_photos("me")
                    uploaded = await client.upload_file(photo_path)
                    await client(UploadProfilePhotoRequest(file=uploaded))
                    if old_photos:
                        await client(DeletePhotosRequest(id=old_photos))
                    acc["dp_type"] = "custom"
                    changed_count += 1
                else:
                    existing_photos = await client.get_profile_photos("me")
                    if existing_photos:
                        await client(DeletePhotosRequest(id=existing_photos))
                    acc["dp_type"] = "default"
                    defaulted_count += 1
            except FloodWaitError as fw:
                errors.append(f"{acc['phone']}: FloodWait {fw.seconds}s")
            except Exception as exc:
                errors.append(f"{acc['phone']}: {exc}")
            finally:
                await safe_disconnect(client)

            if pos < total - 1 and delay_seconds > 0:
                if await sleep_with_countdown(progress_msg, "Updating Profile Photos", pos + 1, total, delay_seconds, cancel_ev):
                    cancelled = True
                    break
    finally:
        active_cancels.pop(event_chat_id, None)
        # Wipe temporary photos immediately so none remain saved in the bot
        save_store(store)
        for fp in list(dp_files):
            try:
                os.remove(fp)
            except OSError:
                pass
        await cleanup_state(event_chat_id)

    lines = [
        f"<code>{render_progress_bar(changed_count + defaulted_count, total)}</code>",
        f"• <b>Updated DP (old deleted):</b> <code>{changed_count}</code>",
        f"• <b>Reset to Default DP:</b> <code>{defaulted_count}</code>",
        f"• <b>Delay per account:</b> <code>{esc(format_delay(delay_seconds))}</code>",
        "• <b>Saved photos in bot:</b> <code>0 (wiped)</code>",
    ]
    if cancelled:
        lines.append("• <i>Stopped early via /cancel.</i>")
    if len(dp_files) > total and not (len(dp_files) == 1 and apply_single_to_all):
        lines.append(f"• <b>Unused extra DPs:</b> <code>{len(dp_files) - total}</code>")
    if errors:
        lines.append("")
        lines.extend(f"⚠ <code>{esc(e)}</code>" for e in errors[:8])
    return format_card("DP Update Stopped" if cancelled else "DP Update Complete", lines)


async def execute_join_batch(
    chat_id: int,
    progress_msg: Any,
    join_type: str,
    join_value: str,
    join_raw: str,
    requested_x: int,
    delay_seconds: float,
) -> str:
    store = load_store()
    accounts = store["accounts"]
    if not accounts:
        await cleanup_state(chat_id)
        return format_card("Empty Fleet", ["No accounts added yet."])

    count = min(requested_x, len(accounts))
    chosen_accounts = random.sample(accounts, count)

    joined = 0
    join_requests = 0
    already_in = 0
    errors: list[str] = []
    last_edit = 0.0
    cancelled = False

    cancel_ev = asyncio.Event()
    active_cancels[chat_id] = cancel_ev
    try:
        for idx, acc in enumerate(chosen_accounts):
            if cancel_ev.is_set():
                cancelled = True
                break
            now = time.monotonic()
            if now - last_edit >= 0.7 or idx == 0:
                spinner = SPINNER_FRAMES[idx % len(SPINNER_FRAMES)]
                pulse = PULSE_FRAMES[idx % len(PULSE_FRAMES)]
                await safe_edit(
                    progress_msg,
                    format_card(
                        "Joining Target",
                        [
                            f"<code>{spinner} {esc(acc['phone'])} ({idx + 1}/{count})</code>",
                            f"<code>{render_progress_bar(idx, count)}</code>",
                        ],
                        icon=pulse,
                    ),
                )
                last_edit = now

            client = create_client(StringSession(acc["session"]))
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    errors.append(f"{acc['phone']}: session expired")
                    continue

                if join_type == "invite":
                    res = await client(ImportChatInviteRequest(join_value))
                    chat_obj = res.chats[0] if getattr(res, "chats", None) else None
                    cid = None
                    if chat_obj is not None:
                        raw_id = getattr(chat_obj, "id", None)
                        cid = int(f"-100{raw_id}") if isinstance(chat_obj, Channel) and raw_id else (-int(raw_id) if isinstance(chat_obj, Chat) and raw_id else raw_id)
                        upsert_account_channel(
                            acc,
                            cid=cid,
                            title=getattr(chat_obj, "title", None) or join_raw,
                            username=getattr(chat_obj, "username", None),
                            target=join_raw,
                        )
                    else:
                        upsert_account_channel(acc, title=join_raw, target=join_raw)
                else:
                    entity = await client.get_entity(join_value)
                    await client(JoinChannelRequest(entity))
                    raw_id = getattr(entity, "id", None)
                    cid = int(f"-100{raw_id}") if isinstance(entity, Channel) and raw_id else (-int(raw_id) if isinstance(entity, Chat) and raw_id else raw_id)
                    upsert_account_channel(
                        acc,
                        cid=cid,
                        title=getattr(entity, "title", None) or getattr(entity, "username", None) or join_raw,
                        username=getattr(entity, "username", None),
                        target=join_raw,
                    )
                joined += 1
            except InviteRequestSentError:
                join_requests += 1
            except UserAlreadyParticipantError:
                already_in += 1
                if join_type != "invite":
                    try:
                        entity = await client.get_entity(join_value)
                        raw_id = getattr(entity, "id", None)
                        cid = int(f"-100{raw_id}") if isinstance(entity, Channel) and raw_id else (-int(raw_id) if isinstance(entity, Chat) and raw_id else raw_id)
                        upsert_account_channel(
                            acc,
                            cid=cid,
                            title=getattr(entity, "title", None) or getattr(entity, "username", None) or join_raw,
                            username=getattr(entity, "username", None),
                            target=join_raw,
                        )
                    except Exception:
                        upsert_account_channel(acc, username=join_value, title=join_raw, target=join_raw)
            except ChannelPrivateError:
                errors.append(f"{acc['phone']}: private channel/link inaccessible")
            except FloodWaitError as fw:
                errors.append(f"{acc['phone']}: FloodWait {fw.seconds}s")
            except Exception as exc:
                errors.append(f"{acc['phone']}: {exc}")
            finally:
                await safe_disconnect(client)

            if idx < count - 1 and delay_seconds > 0:
                if await sleep_with_countdown(progress_msg, "Joining Target", idx + 1, count, delay_seconds, cancel_ev):
                    cancelled = True
                    break
    finally:
        active_cancels.pop(chat_id, None)
        save_store(store)
        await cleanup_state(chat_id)

    done = joined + join_requests + already_in + len(errors)
    lines = [
        f"<code>{render_progress_bar(done if cancelled else count, count)}</code>",
        f"• <b>Target:</b> <code>{esc(join_raw)}</code>",
        f"• <b>Delay per account:</b> <code>{esc(format_delay(delay_seconds))}</code>",
        f"• <b>Joined directly:</b> <code>{joined}</code>",
        f"• <b>Join request sent:</b> <code>{join_requests}</code>",
        f"• <b>Already a member:</b> <code>{already_in}</code>",
    ]
    if cancelled:
        lines.append("• <i>Stopped early via /cancel.</i>")
    if requested_x > len(accounts):
        lines.append(f"• <i>Capped at {len(accounts)} available account(s).</i>")
    if errors:
        lines.append("")
        lines.extend(f"⚠ <code>{esc(e)}</code>" for e in errors[:8])
    return format_card("Join Stopped" if cancelled else "Join Summary", lines)


async def execute_leave_batch(
    chat_id: int,
    progress_msg: Any,
    leave_type: str,
    leave_value: str,
    leave_raw: str,
    target_leaves: int,
    delay_seconds: float,
) -> str:
    store = load_store()
    accounts = store["accounts"]
    if not accounts:
        await cleanup_state(chat_id)
        return format_card("Empty Fleet", ["No accounts added yet."])

    # Prioritize accounts known to be in the selected channel first
    lv_norm = leave_value.strip().lower().lstrip("@")
    known_members: list[dict[str, Any]] = []
    other_accounts: list[dict[str, Any]] = []
    for acc in accounts:
        in_cached = any(
            (leave_type == "chat_id" and str(ch.get("id") or "") == leave_value.strip())
            or (leave_type == "username" and str(ch.get("username") or "").lower().lstrip("@") == lv_norm)
            for ch in (acc.get("channels", []) or [])
        )
        if in_cached:
            known_members.append(acc)
        else:
            other_accounts.append(acc)
    random.shuffle(known_members)
    random.shuffle(other_accounts)
    ordered_accounts = known_members + other_accounts

    target_leaves = min(target_leaves, len(ordered_accounts))
    left_count = 0
    not_member = 0
    errors: list[str] = []
    last_edit = 0.0
    checked = 0
    cancelled = False

    cancel_ev = asyncio.Event()
    active_cancels[chat_id] = cancel_ev
    try:
        for acc in ordered_accounts:
            if left_count >= target_leaves or cancel_ev.is_set():
                if cancel_ev.is_set():
                    cancelled = True
                break
            checked += 1
            now = time.monotonic()
            if now - last_edit >= 0.7 or checked == 1:
                spinner = SPINNER_FRAMES[checked % len(SPINNER_FRAMES)]
                pulse = PULSE_FRAMES[checked % len(PULSE_FRAMES)]
                await safe_edit(
                    progress_msg,
                    format_card(
                        "Leaving Channel",
                        [
                            f"• <b>Channel:</b> {esc(leave_raw)}",
                            f"<code>{spinner} {esc(acc['phone'])} · Left {left_count}/{target_leaves}</code>",
                            f"<code>{render_progress_bar(left_count, target_leaves)}</code>",
                        ],
                        icon=pulse,
                    ),
                )
                last_edit = now

            client = create_client(StringSession(acc["session"]))
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    errors.append(f"{acc['phone']}: session expired")
                    continue

                result = await leave_target_with_client(client, leave_type, leave_value)
                if result == "left":
                    left_count += 1
                    remove_account_channel(acc, leave_type, leave_value, leave_raw)
                else:
                    not_member += 1
                    remove_account_channel(acc, leave_type, leave_value, leave_raw)
            except UserNotParticipantError:
                not_member += 1
                remove_account_channel(acc, leave_type, leave_value, leave_raw)
            except ChannelPrivateError:
                not_member += 1
                remove_account_channel(acc, leave_type, leave_value, leave_raw)
            except FloodWaitError as fw:
                errors.append(f"{acc['phone']}: FloodWait {fw.seconds}s")
            except Exception as exc:
                errors.append(f"{acc['phone']}: {exc}")
            finally:
                await safe_disconnect(client)

            if left_count < target_leaves and checked < len(ordered_accounts) and delay_seconds > 0:
                if await sleep_with_countdown(progress_msg, "Leaving Channel", left_count, target_leaves, delay_seconds, cancel_ev):
                    cancelled = True
                    break
    finally:
        active_cancels.pop(chat_id, None)
        save_store(store)
        await cleanup_state(chat_id)

    lines = [
        f"<code>{render_progress_bar(left_count, target_leaves)}</code>",
        f"• <b>Channel:</b> <code>{esc(leave_raw)}</code>",
        f"• <b>Delay per account:</b> <code>{esc(format_delay(delay_seconds))}</code>",
        f"• <b>Accounts left:</b> <code>{left_count}</code>",
        f"• <b>Not a member:</b> <code>{not_member}</code>",
    ]
    if cancelled:
        lines.append("• <i>Stopped early via /cancel.</i>")
    if errors:
        lines.append("")
        lines.extend(f"⚠ <code>{esc(e)}</code>" for e in errors[:8])
    return format_card("Leave Stopped" if cancelled else "Leave Summary", lines)


async def scan_account_channels_with_client(client: TelegramClient, acc: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    async for dialog in client.iter_dialogs(limit=150):
        if dialog.is_channel or dialog.is_group:
            entity = dialog.entity
            title = (dialog.name or getattr(entity, "title", None) or "Unnamed Channel").strip()
            username = getattr(entity, "username", None)
            cid = int(dialog.id)
            found.append(
                {
                    "id": cid,
                    "title": title,
                    "username": username,
                    "target": f"@{username}" if username else str(cid),
                }
            )
    acc["channels"] = found
    return found


async def scan_fleet_channels(chat_id: int, progress_msg: Any, target_indices: list[int] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    store = load_store()
    accounts = store["accounts"]
    indices = target_indices if target_indices is not None else list(range(len(accounts)))
    total = len(indices)
    errors: list[str] = []
    cancel_ev = asyncio.Event()
    active_cancels[chat_id] = cancel_ev
    last_edit = 0.0

    try:
        for step_pos, acc_idx in enumerate(indices):
            if cancel_ev.is_set() or acc_idx >= len(accounts):
                break
            acc = accounts[acc_idx]
            now = time.monotonic()
            if now - last_edit >= 0.6 or step_pos == 0:
                spinner = SPINNER_FRAMES[step_pos % len(SPINNER_FRAMES)]
                await safe_edit(
                    progress_msg,
                    format_card(
                        "Scanning Joined Channels",
                        [
                            f"<code>{spinner} Reading channels on {esc(acc['phone'])} ({step_pos + 1}/{total})</code>",
                            f"<code>{render_progress_bar(step_pos, total)}</code>",
                        ],
                        icon="◈",
                    ),
                )
                last_edit = now

            client = create_client(StringSession(acc["session"]))
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    errors.append(f"{acc['phone']}: session expired")
                    continue
                await scan_account_channels_with_client(client, acc)
            except FloodWaitError as fw:
                errors.append(f"{acc['phone']}: FloodWait {fw.seconds}s")
            except Exception as exc:
                errors.append(f"{acc['phone']}: {exc}")
            finally:
                await safe_disconnect(client)
    finally:
        active_cancels.pop(chat_id, None)
        save_store(store)

    return aggregate_fleet_channels(store["accounts"]), errors


async def leave_target_with_client(
    client: TelegramClient,
    leave_type: str,
    leave_value: str,
) -> str:
    """
    Leaves a channel/supergroup/group.
    Returns: 'left' | 'not_member'
    """
    entity: Any = None
    if leave_type == "invite":
        res = await client(CheckChatInviteRequest(leave_value))
        if isinstance(res, ChatInviteAlready):
            entity = res.chat
        else:
            return "not_member"
    elif leave_type == "chat_id":
        target_cid = int(leave_value)
        try:
            entity = await client.get_entity(target_cid)
        except Exception:
            async for dialog in client.iter_dialogs(limit=200):
                if int(dialog.id) == target_cid or int(getattr(dialog.entity, "id", 0)) == abs(target_cid):
                    entity = dialog.entity
                    break
            if entity is None:
                return "not_member"
    else:
        try:
            entity = await client.get_entity(leave_value)
        except Exception:
            lv = leave_value.strip().lower().lstrip("@")
            async for dialog in client.iter_dialogs(limit=200):
                uname = (getattr(dialog.entity, "username", None) or "").lower()
                if uname == lv or (dialog.name or "").strip().lower() == lv:
                    entity = dialog.entity
                    break
            if entity is None:
                return "not_member"

    if isinstance(entity, Channel):
        await client(LeaveChannelRequest(entity))
        return "left"
    if isinstance(entity, Chat):
        await client(DeleteChatUserRequest(entity.id, "me"))
        return "left"

    await client.delete_dialog(entity)
    return "left"


# ---------------------------------------------------------------------------
# Bot Entrypoint
# ---------------------------------------------------------------------------
def _reset_bot_session_files() -> None:
    base = Path(__file__).resolve().parent
    for suffix in (".session", ".session-journal", ".session-wal", ".session-shm"):
        p = base / f"telefleet_bot{suffix}"
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


async def run_bot_session(bot: TelegramClient) -> None:
    await bot.start(bot_token=BOT_TOKEN)
    print("TeleFleet Pro bot is running...")

    # -------------------- /start & /help --------------------
    @bot.on(events.NewMessage(pattern=r"^/(start|help)$"))
    async def cmd_start(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        await cleanup_state(event.chat_id)
        owner_view = is_owner(event.sender_id, store)
        msg = await play_intro_animation(event, len(store["accounts"]), owner_view)
        menu_text, buttons = build_menu_payload(store, owner_view)
        await safe_edit(msg, menu_text, buttons=buttons)

    # -------------------- Owner Approval Commands --------------------
    @bot.on(events.NewMessage(pattern=r"^/(approve|allow)(?:\s+(.+))?$"))
    async def cmd_approve(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_owner(event.sender_id, store):
            await event.reply(
                format_card("Owner Only", ["Only the bot owner can approve member Chat IDs."]),
                parse_mode="html",
            )
            return
        arg = (event.pattern_match.group(2) or "").strip()
        if not re.match(r"^-?\d+$", arg):
            await event.reply(
                format_card(
                    "Approve Member",
                    [
                        "Usage: <code>/approve 123456789</code>",
                        "Have the member message the bot once to see their Chat ID.",
                    ],
                ),
                parse_mode="html",
            )
            return

        target_id = int(arg)
        approved = [int(x) for x in store.get("approved_ids", [])]
        if target_id == get_owner_id(store):
            await event.reply(
                format_card("Already Owner", [f"Chat ID <code>{target_id}</code> is the bot owner."]),
                parse_mode="html",
            )
            return
        if target_id in approved:
            await event.reply(
                format_card("Already Approved", [f"Chat ID <code>{target_id}</code> is already approved."]),
                parse_mode="html",
            )
            return

        approved.append(target_id)
        store["approved_ids"] = approved
        save_store(store)
        await event.reply(
            format_card(
                "Member Approved",
                [
                    f"• <b>Approved Chat ID:</b> <code>{target_id}</code>",
                    f"• <b>Total Approved:</b> <code>{len(approved)}</code>",
                ],
            ),
            parse_mode="html",
        )

    @bot.on(events.NewMessage(pattern=r"^/(unapprove|revoke)(?:\s+(.+))?$"))
    async def cmd_unapprove(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_owner(event.sender_id, store):
            await event.reply(
                format_card("Owner Only", ["Only the bot owner can revoke member Chat IDs."]),
                parse_mode="html",
            )
            return
        arg = (event.pattern_match.group(2) or "").strip()
        if not re.match(r"^-?\d+$", arg):
            await event.reply(
                format_card("Revoke Member", ["Usage: <code>/unapprove 123456789</code>"]),
                parse_mode="html",
            )
            return

        target_id = int(arg)
        approved = [int(x) for x in store.get("approved_ids", [])]
        if target_id not in approved:
            await event.reply(
                format_card("Not Found", [f"Chat ID <code>{target_id}</code> is not in the approved list."]),
                parse_mode="html",
            )
            return

        approved.remove(target_id)
        store["approved_ids"] = approved
        save_store(store)
        await event.reply(
            format_card(
                "Access Revoked",
                [
                    f"• <b>Removed Chat ID:</b> <code>{target_id}</code>",
                    f"• <b>Remaining Approved:</b> <code>{len(approved)}</code>",
                ],
            ),
            parse_mode="html",
        )

    @bot.on(events.NewMessage(pattern=r"^/(users|approved)$"))
    async def cmd_users(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_owner(event.sender_id, store):
            await event.reply(
                format_card("Owner Only", ["Only the bot owner can view approved Chat IDs."]),
                parse_mode="html",
            )
            return
        owner_id = get_owner_id(store)
        approved = [int(x) for x in store.get("approved_ids", [])]
        lines = [f"• <b>Owner ID:</b> <code>{owner_id}</code>"]
        if not approved:
            lines.append("• <b>Approved Members:</b> <i>None yet</i>")
        else:
            lines.append("• <b>Approved Members:</b>")
            for idx, uid in enumerate(approved, start=1):
                lines.append(f"  <code>{idx:02d}.</code> <code>{uid}</code>")
        await event.reply(format_card("Authorized Users", lines), parse_mode="html")

    # -------------------- Core Commands --------------------
    @bot.on(events.NewMessage(pattern=r"^/cancel$"))
    async def cmd_cancel(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        await cleanup_state(event.chat_id)
        await event.reply(format_card("Cancelled", ["Active operation cancelled."]), parse_mode="html")

    def _short_btn(text: str, limit: int = 28) -> str:
        clean = " ".join(text.split())
        return clean if len(clean) <= limit else clean[: limit - 1] + "…"

    async def send_accounts_list(chat_id: int, respond_fn: Any, edit_msg: Any = None) -> None:
        store = load_store()
        accounts = store["accounts"]
        if not accounts:
            payload = format_card("Empty Fleet", ["No accounts added yet. Send <code>/add</code> to add your first account."])
            if edit_msg is not None:
                await safe_edit(edit_msg, payload)
            else:
                await respond_fn(payload, parse_mode="html")
            return

        lines = []
        acc_buttons: list[list[Any]] = []
        row: list[Any] = []
        for idx, acc in enumerate(accounts, start=1):
            dp_badge = "Custom DP" if acc.get("dp_type") == "custom" else "Default DP"
            ch_count = len(acc.get("channels", []) or [])
            ch_tag = f" · {ch_count} ch" if ch_count > 0 else ""
            lines.append(
                f"<code>{idx:02d}.</code> <code>{esc(acc['phone'])}</code> · <b>{esc(acc.get('name', 'Account'))}</b> · <i>{dp_badge}{ch_tag}</i>"
            )
            label = f"{idx:02d} · {_short_btn(acc.get('name') or acc['phone'], 16)}"
            row.append(Button.inline(label, f"accview:{idx - 1}".encode()))
            if len(row) == 2:
                acc_buttons.append(row)
                row = []
        if row:
            acc_buttons.append(row)

        acc_buttons.append(
            [
                Button.inline("↻ Scan Live Channels", b"scan_channels"),
                Button.inline("📢 Channels / Leave", b"menu_channels"),
            ]
        )
        card = format_card(
            f"Added Accounts ({len(accounts)}/{MAX_ACCOUNTS})",
            lines,
            "Tap any account below to inspect or leave its channels.",
        )
        if edit_msg is not None:
            await safe_edit(edit_msg, card, buttons=acc_buttons)
        else:
            await respond_fn(card, parse_mode="html", buttons=acc_buttons)

    async def send_account_detail(chat_id: int, acc_idx: int, respond_fn: Any, edit_msg: Any = None) -> None:
        store = load_store()
        accounts = store["accounts"]
        if acc_idx < 0 or acc_idx >= len(accounts):
            await send_accounts_list(chat_id, respond_fn, edit_msg=edit_msg)
            return
        acc = accounts[acc_idx]
        channels = acc.get("channels", []) or []
        dp_badge = "Custom DP" if acc.get("dp_type") == "custom" else "Default DP"
        lines = [
            f"• <b>Slot:</b> <code>{acc_idx + 1:02d}/{MAX_ACCOUNTS}</code>",
            f"• <b>Phone:</b> <code>{esc(acc['phone'])}</code>",
            f"• <b>Name:</b> {esc(acc.get('name', 'Account'))}",
            f"• <b>Profile Photo:</b> <i>{dp_badge}</i>",
            f"• <b>Joined Channels ({len(channels)}):</b>",
        ]
        buttons: list[list[Any]] = []
        if not channels:
            lines.append("  <i>No channels cached yet. Tap Scan Account below.</i>")
        else:
            for c_idx, ch in enumerate(channels[:12]):
                title = ch.get("title") or ch.get("target") or "Channel"
                uname = f" (@{ch['username']})" if ch.get("username") else ""
                lines.append(f"  <code>{c_idx + 1:02d}.</code> {esc(title)}{esc(uname)}")
                buttons.append(
                    [Button.inline(f"↙ Leave: {_short_btn(title, 26)}", f"acclv:{acc_idx}:{c_idx}".encode())]
                )
            if len(channels) > 12:
                lines.append(f"  <i>+{len(channels) - 12} more in /channels</i>")

        buttons.append(
            [
                Button.inline("↻ Scan This Account", f"accscan:{acc_idx}".encode()),
                Button.inline("◀ Back to Accounts", b"menu_list"),
            ]
        )
        card = format_card(
            f"Account #{acc_idx + 1:02d}",
            lines,
            "Tap a channel button above to leave it from this account.",
        )
        if edit_msg is not None:
            await safe_edit(edit_msg, card, buttons=buttons)
        else:
            await respond_fn(card, parse_mode="html", buttons=buttons)

    async def send_channels_picker(
        chat_id: int,
        respond_fn: Any,
        page: int = 0,
        force_scan: bool = False,
        edit_msg: Any = None,
    ) -> None:
        store = load_store()
        accounts = store["accounts"]
        if not accounts:
            payload = format_card("Empty Fleet", ["No accounts added yet. Use <code>/add</code> first."])
            if edit_msg is not None:
                await safe_edit(edit_msg, payload)
            else:
                await respond_fn(payload, parse_mode="html")
            return

        channels = aggregate_fleet_channels(accounts)
        scan_errors: list[str] = []
        if force_scan or not channels:
            progress_msg = edit_msg
            if progress_msg is None:
                progress_msg = await respond_fn(
                    format_card("Scanning Joined Channels", ["<code>⠋ Reading channels across your accounts...</code>"], icon="◈"),
                    parse_mode="html",
                )
            else:
                await safe_edit(
                    progress_msg,
                    format_card("Scanning Joined Channels", ["<code>⠋ Reading channels across your accounts...</code>"], icon="◈"),
                )
            channels, scan_errors = await scan_fleet_channels(chat_id, progress_msg)
            edit_msg = progress_msg

        channel_list_cache[chat_id] = channels
        user_states[chat_id] = {"step": "awaiting_leave_link"}

        if not channels:
            lines = ["No joined channels or groups found across your accounts."]
            if scan_errors:
                lines.append("")
                lines.extend(f"⚠ <code>{esc(e)}</code>" for e in scan_errors[:5])
            buttons = [
                [Button.inline("↻ Rescan Channels", b"scan_channels"), Button.inline("✎ Enter Link Manually", b"leave_manual")],
            ]
            card = format_card("No Channels Found", lines, "You can also paste a t.me link or @username.")
            if edit_msg is not None:
                await safe_edit(edit_msg, card, buttons=buttons)
            else:
                await respond_fn(card, parse_mode="html", buttons=buttons)
            return

        per_page = 8
        total_pages = max(1, (len(channels) + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        start = page * per_page
        chunk = channels[start : start + per_page]

        lines = [f"Found <code>{len(channels)}</code> channel(s)/group(s) across <code>{len(accounts)}</code> account(s):"]
        buttons: list[list[Any]] = []
        for offset, ch in enumerate(chunk):
            g_idx = start + offset
            cnt = len(ch["phones"])
            uname_str = f" · @{ch['username']}" if ch.get("username") else ""
            lines.append(f"<code>{g_idx + 1:02d}.</code> <b>{esc(ch['title'])}</b>{esc(uname_str)} (<code>{cnt}</code> accs)")
            buttons.append(
                [Button.inline(f"↙ {_short_btn(ch['title'], 25)} ({cnt})", f"lvch:{g_idx}".encode())]
            )

        if total_pages > 1:
            nav_row: list[Any] = []
            if page > 0:
                nav_row.append(Button.inline("◀ Prev", f"lvpage:{page - 1}".encode()))
            if page < total_pages - 1:
                nav_row.append(Button.inline("Next ▶", f"lvpage:{page + 1}".encode()))
            if nav_row:
                buttons.append(nav_row)

        buttons.append(
            [
                Button.inline("↻ Rescan Channels", b"scan_channels"),
                Button.inline("✎ Type Link Manually", b"leave_manual"),
            ]
        )
        card = format_card(
            f"Channels ({len(channels)}) · Page {page + 1}/{total_pages}",
            lines,
            "Tap any channel name button to leave it, or paste a link.",
        )
        if edit_msg is not None:
            await safe_edit(edit_msg, card, buttons=buttons)
        else:
            await respond_fn(card, parse_mode="html", buttons=buttons)

    @bot.on(events.NewMessage(pattern=r"^/(list|accounts)$"))
    async def cmd_list(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        await send_accounts_list(event.chat_id, event.reply)

    @bot.on(events.NewMessage(pattern=r"^/channels$"))
    async def cmd_channels(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        await cleanup_state(event.chat_id)
        await send_channels_picker(event.chat_id, event.reply, page=0, force_scan=False)

    @bot.on(events.NewMessage(pattern=r"^/remove(?:\s+(.+))?$"))
    async def cmd_remove(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await event.reply(
                format_card("Remove Account", ["Usage: <code>/remove 1</code> or <code>/remove +14155550101</code>"]),
                parse_mode="html",
            )
            return
        accounts = store["accounts"]
        removed = None
        if arg.isdigit():
            slot = int(arg)
            if 1 <= slot <= len(accounts):
                removed = accounts.pop(slot - 1)
        else:
            norm = normalize_phone(arg)
            for i, acc in enumerate(accounts):
                if acc["phone"] == (norm or arg):
                    removed = accounts.pop(i)
                    break
        if not removed:
            await event.reply(format_card("Not Found", ["Account not found in fleet."]), parse_mode="html")
            return
        save_store(store)
        await event.reply(
            format_card(
                "Account Removed",
                [
                    f"• <b>Removed:</b> <code>{esc(removed['phone'])}</code>",
                    f"• <b>Remaining:</b> <code>{len(accounts)}/{MAX_ACCOUNTS}</code>",
                ],
            ),
            parse_mode="html",
        )

    async def start_add_flow(chat_id: int, respond_fn: Any) -> None:
        store = load_store()
        await cleanup_state(chat_id)
        if len(store["accounts"]) >= MAX_ACCOUNTS:
            await respond_fn(
                format_card("Capacity Reached", [f"Max limit reached (<code>{MAX_ACCOUNTS}/{MAX_ACCOUNTS}</code>). Remove an account first."]),
                parse_mode="html",
            )
            return
        user_states[chat_id] = {"step": "awaiting_phone"}
        await respond_fn(
            format_card(
                f"Add Account · Slot {len(store['accounts']) + 1}/{MAX_ACCOUNTS}",
                [
                    "Send the Telegram phone number with country code.",
                    "Example: <code>+919876543210</code>",
                ],
            ),
            parse_mode="html",
        )

    async def start_names_flow(chat_id: int, respond_fn: Any) -> None:
        store = load_store()
        await cleanup_state(chat_id)
        if not store["accounts"]:
            await respond_fn(format_card("Empty Fleet", ["No accounts added yet. Use <code>/add</code> first."]), parse_mode="html")
            return
        user_states[chat_id] = {"step": "awaiting_names"}
        await respond_fn(
            format_card(
                "Random Name Batcher",
                [
                    "Send <b>X</b> names (one per line or comma-separated).",
                    f"Exactly <b>X</b> random accounts out of <code>{len(store['accounts'])}</code> will be renamed.",
                ],
            ),
            parse_mode="html",
        )

    async def start_dp_flow(chat_id: int, respond_fn: Any) -> None:
        store = load_store()
        await cleanup_state(chat_id)
        if not store["accounts"]:
            await respond_fn(format_card("Empty Fleet", ["No accounts added yet. Use <code>/add</code> first."]), parse_mode="html")
            return
        user_states[chat_id] = {"step": "awaiting_dps", "dp_files": []}
        await respond_fn(
            format_card(
                "Batch Profile Photos",
                [
                    f"Send your DP photo(s) now (<code>1</code> to <code>{len(store['accounts'])}</code>).",
                    "• Old DPs on updated accounts are deleted automatically.",
                    "• No photos stay saved inside the bot.",
                    "• Remaining accounts without a DP reset to Default DP.",
                ],
            ),
            parse_mode="html",
            buttons=[[Button.inline("✦ Apply DPs Now", b"apply_dp_batch")]],
        )

    async def start_join_flow(chat_id: int, respond_fn: Any) -> None:
        store = load_store()
        await cleanup_state(chat_id)
        if not store["accounts"]:
            await respond_fn(format_card("Empty Fleet", ["No accounts added yet. Use <code>/add</code> first."]), parse_mode="html")
            return
        user_states[chat_id] = {"step": "awaiting_join_link"}
        await respond_fn(
            format_card(
                "Join / Request Link",
                [
                    "Send the Telegram group or channel link (or <code>@username</code>).",
                    "Supports public links (<code>t.me/name</code>) &amp; private invites (<code>t.me/+hash</code>).",
                ],
            ),
            parse_mode="html",
        )

    async def start_leave_flow(chat_id: int, respond_fn: Any) -> None:
        await cleanup_state(chat_id)
        await send_channels_picker(chat_id, respond_fn, page=0, force_scan=False)

    async def start_delay_flow(chat_id: int, respond_fn: Any) -> None:
        store = load_store()
        await cleanup_state(chat_id)
        current = float(store.get("delay_seconds", 1.0))
        user_states[chat_id] = {"step": "awaiting_global_delay"}
        await respond_fn(
            format_card(
                "Request Time Per Account",
                [
                    f"• <b>Current default delay:</b> <code>{esc(format_delay(current))}</code>",
                    "• Send the delay time between each account request.",
                    "• Examples: <code>0</code>, <code>5</code>, <code>10s</code>, <code>1m</code>, <code>1m30s</code>",
                ],
            ),
            parse_mode="html",
            buttons=[
                [
                    Button.inline("0s (Instant)", b"setdelay:0"),
                    Button.inline("3s", b"setdelay:3"),
                    Button.inline("5s", b"setdelay:5"),
                ],
                [
                    Button.inline("10s", b"setdelay:10"),
                    Button.inline("30s", b"setdelay:30"),
                    Button.inline("1m", b"setdelay:60"),
                ],
            ],
        )

    @bot.on(events.NewMessage(pattern=r"^/add$"))
    async def cmd_add(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        await start_add_flow(event.chat_id, event.reply)

    @bot.on(events.NewMessage(pattern=r"^/names$"))
    async def cmd_names(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        await start_names_flow(event.chat_id, event.reply)

    @bot.on(events.NewMessage(pattern=r"^/dp$"))
    async def cmd_dp(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        await start_dp_flow(event.chat_id, event.reply)

    @bot.on(events.NewMessage(pattern=r"^/join$"))
    async def cmd_join(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        await start_join_flow(event.chat_id, event.reply)

    @bot.on(events.NewMessage(pattern=r"^/leave$"))
    async def cmd_leave(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        await start_leave_flow(event.chat_id, event.reply)

    @bot.on(events.NewMessage(pattern=r"^/(delay|time)(?:\s+(.+))?$"))
    async def cmd_delay(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        arg = (event.pattern_match.group(2) or "").strip()
        if not arg:
            await start_delay_flow(event.chat_id, event.reply)
            return
        parsed = parse_delay_seconds(arg)
        if parsed is None:
            await event.reply(
                format_card("Invalid Delay", ["Send a valid time like <code>0</code>, <code>5</code>, <code>10s</code>, or <code>1m</code> (max 1h)."]),
                parse_mode="html",
            )
            return
        store["delay_seconds"] = parsed
        save_store(store)
        await event.reply(
            format_card(
                "Delay Updated",
                [f"• <b>Default time per account:</b> <code>{esc(format_delay(parsed))}</code>"],
                icon="✔",
            ),
            parse_mode="html",
        )

    @bot.on(events.NewMessage(pattern=r"^/apply_dp$"))
    async def cmd_apply_dp(event: events.NewMessage.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return
        state = user_states.get(event.chat_id)
        if not state or state.get("step") != "awaiting_dps" or not state.get("dp_files"):
            await event.reply(
                format_card("No Photos Queued", ["Send <code>/dp</code> and upload photos first."]),
                parse_mode="html",
            )
            return
        progress_msg = await event.reply(
            format_card("Updating Profile Photos", ["<code>⠋ Starting batch DP update...</code>"]),
            parse_mode="html",
        )
        summary = await apply_dp_batch(event.chat_id, progress_msg, state["dp_files"], apply_single_to_all=False)
        await safe_edit(progress_msg, summary)

    # -------------------- Inline Menu Callbacks --------------------
    @bot.on(events.CallbackQuery(pattern=rb"^menu_(add|names|dp|join|leave|delay|list|channels|refresh)$"))
    async def cb_menu_action(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Not approved. Message bot for your Chat ID.", alert=True)
            return
        action = event.data.decode("utf-8").split("_", 1)[1]
        await event.answer()
        if action == "refresh":
            owner_view = is_owner(event.sender_id, store)
            msg = await event.get_message()
            await safe_edit(
                msg,
                format_card("TeleFleet Pro", ["<code>⠹ Refreshing fleet status...</code>"], icon="◈"),
            )
            await asyncio.sleep(0.25)
            menu_text, buttons = build_menu_payload(store, owner_view)
            await safe_edit(msg, menu_text, buttons=buttons)
            return
        if action == "add":
            await start_add_flow(event.chat_id, event.respond)
        elif action == "names":
            await start_names_flow(event.chat_id, event.respond)
        elif action == "dp":
            await start_dp_flow(event.chat_id, event.respond)
        elif action == "join":
            await start_join_flow(event.chat_id, event.respond)
        elif action in ("leave", "channels"):
            msg = await event.get_message()
            # Edit if triggered from sub-view, otherwise send fresh channel picker
            if action == "channels" and msg and "Command Center" not in (msg.raw_text or ""):
                await send_channels_picker(event.chat_id, event.respond, page=0, force_scan=False, edit_msg=msg)
            else:
                await start_leave_flow(event.chat_id, event.respond)
        elif action == "delay":
            await start_delay_flow(event.chat_id, event.respond)
        elif action == "list":
            msg = await event.get_message()
            if msg and "Account #" in (msg.raw_text or ""):
                await send_accounts_list(event.chat_id, event.respond, edit_msg=msg)
            else:
                await send_accounts_list(event.chat_id, event.respond)

    @bot.on(events.CallbackQuery(pattern=rb"^accview:(\d+)$"))
    async def cb_account_view(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        acc_idx = int(event.pattern_match.group(1).decode("utf-8"))
        await event.answer()
        msg = await event.get_message()
        await send_account_detail(event.chat_id, acc_idx, event.respond, edit_msg=msg)

    @bot.on(events.CallbackQuery(pattern=rb"^accscan:(\d+)$"))
    async def cb_account_scan(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        acc_idx = int(event.pattern_match.group(1).decode("utf-8"))
        await event.answer("Scanning account channels...")
        msg = await event.get_message()
        await scan_fleet_channels(event.chat_id, msg, target_indices=[acc_idx])
        await send_account_detail(event.chat_id, acc_idx, event.respond, edit_msg=msg)

    @bot.on(events.CallbackQuery(pattern=rb"^acclv:(\d+):(\d+)$"))
    async def cb_account_leave_channel(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        acc_idx = int(event.pattern_match.group(1).decode("utf-8"))
        ch_idx = int(event.pattern_match.group(2).decode("utf-8"))
        accounts = store["accounts"]
        if acc_idx >= len(accounts):
            await event.answer("Account not found.", alert=True)
            return
        acc = accounts[acc_idx]
        channels = acc.get("channels", []) or []
        if ch_idx >= len(channels):
            await event.answer("Channel not found. Rescan first.", alert=True)
            return
        ch = channels[ch_idx]
        title = ch.get("title") or ch.get("target") or "Channel"
        if ch.get("id"):
            leave_type, leave_value = "chat_id", str(ch["id"])
        elif ch.get("username"):
            leave_type, leave_value = "username", str(ch["username"]).lstrip("@")
        else:
            parsed = parse_join_target(str(ch.get("target") or ""))
            if parsed:
                leave_type, leave_value = parsed
            else:
                leave_type, leave_value = "username", str(ch.get("target") or title).lstrip("@")

        await event.answer(f"Leaving {title}...")
        msg = await event.get_message()
        await safe_edit(
            msg,
            format_card("Leaving Channel", [f"<code>⠋ {esc(acc['phone'])} leaving {esc(title)}...</code>"], icon="◈"),
        )
        client = create_client(StringSession(acc["session"]))
        status_line = ""
        try:
            await client.connect()
            if not await client.is_user_authorized():
                status_line = "⚠ Session expired."
            else:
                res = await leave_target_with_client(client, leave_type, leave_value)
                remove_account_channel(acc, leave_type, leave_value, title)
                save_store(store)
                status_line = f"✔ Left <b>{esc(title)}</b>." if res == "left" else f"• Not a member of <b>{esc(title)}</b>."
        except Exception as exc:
            status_line = f"⚠ Failed: <code>{esc(exc)}</code>"
        finally:
            await safe_disconnect(client)

        await safe_edit(
            msg,
            format_card(
                "Channel Leave Result",
                [f"• <b>Account:</b> <code>{esc(acc['phone'])}</code>", status_line],
                icon="✔",
            ),
            buttons=[[Button.inline("◀ Back to Account", f"accview:{acc_idx}".encode()), Button.inline("☰ All Accounts", b"menu_list")]],
        )

    @bot.on(events.CallbackQuery(data=b"scan_channels"))
    async def cb_scan_channels(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        await event.answer("Scanning all accounts...")
        msg = await event.get_message()
        await send_channels_picker(event.chat_id, event.respond, page=0, force_scan=True, edit_msg=msg)

    @bot.on(events.CallbackQuery(pattern=rb"^lvpage:(\d+)$"))
    async def cb_leave_page(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        page = int(event.pattern_match.group(1).decode("utf-8"))
        await event.answer()
        msg = await event.get_message()
        await send_channels_picker(event.chat_id, event.respond, page=page, force_scan=False, edit_msg=msg)

    @bot.on(events.CallbackQuery(data=b"leave_manual"))
    async def cb_leave_manual(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        await event.answer()
        user_states[event.chat_id] = {"step": "awaiting_leave_link"}
        msg = await event.get_message()
        await safe_edit(
            msg,
            format_card(
                "Leave Channel / Group",
                [
                    "Send the channel/group link, <code>@username</code>, or chat ID to leave.",
                    "Supports public links, private invite links, and numeric IDs.",
                ],
            ),
            buttons=[[Button.inline("📢 Show Channel Buttons", b"menu_channels")]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^lvch:(\d+)$"))
    async def cb_pick_leave_channel(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        idx = int(event.pattern_match.group(1).decode("utf-8"))
        channels = channel_list_cache.get(event.chat_id) or aggregate_fleet_channels(store["accounts"])
        if idx < 0 or idx >= len(channels):
            await event.answer("Channel list refreshed. Pick again.", alert=True)
            return
        ch = channels[idx]
        member_count = max(1, len(ch["phones"]))
        user_states[event.chat_id] = {
            "step": "awaiting_leave_count",
            "leave_type": ch["leave_type"],
            "leave_value": ch["leave_value"],
            "leave_raw": ch["title"],
            "member_count": member_count,
        }
        await event.answer()
        default_delay = float(store.get("delay_seconds", 1.0))

        cnt_row: list[Any] = []
        for c in [1, 2, 3, 5, 10]:
            if c < member_count:
                cnt_row.append(Button.inline(f"Leave {c}", f"lvcnt:{c}".encode()))
        buttons = [
            [Button.inline(f"⚡ Leave All ({member_count}) Now · {format_delay(default_delay)}", b"lvcnt:now")],
        ]
        if cnt_row:
            buttons.append(cnt_row)
        buttons.append([Button.inline(f"Leave All ({member_count}) · Pick Delay", b"lvcnt:all")])
        buttons.append([Button.inline("◀ Back to Channels", b"menu_channels")])

        uname_line = f" (@{ch['username']})" if ch.get("username") else ""
        msg = await event.get_message()
        await safe_edit(
            msg,
            format_card(
                "Confirm Leave Channel",
                [
                    f"• <b>Channel:</b> {esc(ch['title'])}{esc(uname_line)}",
                    f"• <b>Joined Accounts:</b> <code>{member_count}</code>",
                    "• Tap <b>Leave All Now</b>, pick a count, or reply with number <b>X</b>.",
                ],
            ),
            buttons=buttons,
        )

    @bot.on(events.CallbackQuery(pattern=rb"^lvcnt:(now|all|\d+)$"))
    async def cb_leave_count_pick(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        state = user_states.get(event.chat_id)
        if not state or state.get("step") != "awaiting_leave_count":
            await event.answer("Session expired. Open /leave again.", alert=True)
            return
        choice = event.pattern_match.group(1).decode("utf-8")
        accounts = store["accounts"]
        member_max = int(state.get("member_count") or len(accounts))
        default_delay = float(store.get("delay_seconds", 1.0))
        msg = await event.get_message()

        if choice == "now":
            await event.answer(f"Leaving {state['leave_raw']}...")
            await safe_edit(
                msg,
                format_card(
                    "Leaving Channel",
                    [f"<code>⠋ Leaving {esc(state['leave_raw'])} across {member_max} account(s)...</code>"],
                    icon="◈",
                ),
            )
            summary = await execute_leave_batch(
                event.chat_id,
                msg,
                state["leave_type"],
                state["leave_value"],
                state["leave_raw"],
                member_max,
                default_delay,
            )
            await safe_edit(msg, summary)
            return

        target_leaves = member_max if choice == "all" else min(int(choice), len(accounts))
        await event.answer()
        user_states[event.chat_id] = {
            "step": "awaiting_leave_delay",
            "leave_type": state["leave_type"],
            "leave_value": state["leave_value"],
            "leave_raw": state["leave_raw"],
            "target_leaves": target_leaves,
        }
        await safe_edit(
            msg,
            format_card(
                "Time Per Account",
                [
                    f"• <b>Channel:</b> {esc(state['leave_raw'])}",
                    f"• <b>Accounts to leave:</b> <code>{target_leaves}</code>",
                    "• Send delay time between each leave request or tap a button.",
                ],
            ),
            buttons=delay_preset_buttons("leavedelay", default_delay),
        )

    @bot.on(events.CallbackQuery(data=b"apply_dp_batch"))
    async def cb_apply_dp_batch(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        state = user_states.get(event.chat_id)
        if not state or state.get("step") != "awaiting_dps" or not state.get("dp_files"):
            await event.answer("Send at least 1 DP photo first.", alert=True)
            return
        await event.answer("Applying DPs...")
        progress_msg = await event.respond(
            format_card("Updating Profile Photos", ["<code>⠋ Starting batch DP update...</code>"]),
            parse_mode="html",
        )
        summary = await apply_dp_batch(event.chat_id, progress_msg, state["dp_files"], apply_single_to_all=False)
        await safe_edit(progress_msg, summary)

    @bot.on(events.CallbackQuery(data=b"apply_dp_all"))
    async def cb_apply_dp_all(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        state = user_states.get(event.chat_id)
        if not state or state.get("step") != "awaiting_dps" or not state.get("dp_files"):
            await event.answer("Send a DP photo first.", alert=True)
            return
        await event.answer("Applying 1 DP to all accounts...")
        progress_msg = await event.respond(
            format_card("Updating Profile Photos", ["<code>⠋ Applying DP to all accounts...</code>"]),
            parse_mode="html",
        )
        summary = await apply_dp_batch(event.chat_id, progress_msg, state["dp_files"], apply_single_to_all=True)
        await safe_edit(progress_msg, summary)

    @bot.on(events.CallbackQuery(pattern=rb"^setdelay:([0-9.]+)$"))
    async def cb_set_global_delay(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        val = float(event.pattern_match.group(1).decode("utf-8"))
        store["delay_seconds"] = val
        save_store(store)
        await cleanup_state(event.chat_id)
        await event.answer(f"Delay set to {format_delay(val)}")
        msg = await event.get_message()
        await safe_edit(
            msg,
            format_card(
                "Delay Updated",
                [f"• <b>Default time per account:</b> <code>{esc(format_delay(val))}</code>"],
                icon="✔",
            ),
        )

    @bot.on(events.CallbackQuery(pattern=rb"^joindelay:([0-9.]+)$"))
    async def cb_join_delay(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        state = user_states.get(event.chat_id)
        if not state or state.get("step") != "awaiting_join_delay":
            await event.answer("Session expired. Send /join again.", alert=True)
            return
        delay_sec = float(event.pattern_match.group(1).decode("utf-8"))
        await event.answer(f"Starting with {format_delay(delay_sec)} delay...")
        progress_msg = await event.get_message()
        await safe_edit(
            progress_msg,
            format_card(
                "Joining Target",
                [f"<code>⠋ Starting {state['requested_x']} account(s) · {format_delay(delay_sec)} delay...</code>"],
                icon="◈",
            ),
        )
        summary = await execute_join_batch(
            event.chat_id,
            progress_msg,
            state["join_type"],
            state["join_value"],
            state["join_raw"],
            state["requested_x"],
            delay_sec,
        )
        await safe_edit(progress_msg, summary)

    @bot.on(events.CallbackQuery(pattern=rb"^leavedelay:([0-9.]+)$"))
    async def cb_leave_delay(event: events.CallbackQuery.Event) -> None:
        store = load_store()
        if not is_authorized(event.sender_id, store):
            await event.answer("Unauthorized", alert=True)
            return
        state = user_states.get(event.chat_id)
        if not state or state.get("step") != "awaiting_leave_delay":
            await event.answer("Session expired. Send /leave again.", alert=True)
            return
        delay_sec = float(event.pattern_match.group(1).decode("utf-8"))
        await event.answer(f"Leaving with {format_delay(delay_sec)} delay...")
        progress_msg = await event.get_message()
        await safe_edit(
            progress_msg,
            format_card(
                "Leaving Channel",
                [f"<code>⠋ Processing leave · {format_delay(delay_sec)} delay...</code>"],
                icon="◈",
            ),
        )
        summary = await execute_leave_batch(
            event.chat_id,
            progress_msg,
            state["leave_type"],
            state["leave_value"],
            state["leave_raw"],
            state["target_leaves"],
            delay_sec,
        )
        await safe_edit(progress_msg, summary)

    # -------------------- Conversational State Handler --------------------
    @bot.on(events.NewMessage())
    async def handle_messages(event: events.NewMessage.Event) -> None:
        text = (event.raw_text or "").strip()
        if text.startswith("/"):
            return

        store = load_store()
        if not is_authorized(event.sender_id, store):
            await deny_access(event)
            return

        state = user_states.get(event.chat_id)
        if not state:
            return

        step = state.get("step")

        # 1. DP Photo Uploads
        if step == "awaiting_dps":
            if event.photo or (event.document and getattr(event.document, "mime_type", "").startswith("image/")):
                tmp_path = tempfile.mktemp(prefix="telefleet_dp_", suffix=".jpg")
                await event.download_media(file=tmp_path)
                state["dp_files"].append(tmp_path)
                queued = len(state["dp_files"])
                total = len(store["accounts"])
                defaulted = max(0, total - queued)

                buttons = [[Button.inline(f"✦ Apply {queued} DP(s) ({defaulted} Default)", b"apply_dp_batch")]]
                if queued == 1 and total > 1:
                    buttons.append([Button.inline(f"✦ Apply This 1 DP to All {total}", b"apply_dp_all")])

                await event.reply(
                    format_card(
                        f"Queued DP #{queued}",
                        [
                            f"• <b>Will update:</b> <code>{min(queued, total)}</code> account(s)",
                            f"• <b>Will reset to Default DP:</b> <code>{defaulted}</code> account(s)",
                            "Send more photos or tap Apply below.",
                        ],
                    ),
                    parse_mode="html",
                    buttons=buttons,
                )
            else:
                await event.reply(
                    format_card("Photo Expected", ["Please send photo/image files, tap Apply, or send <code>/cancel</code>."]),
                    parse_mode="html",
                )
            return

        # 2. Phone Number -> Send OTP
        if step == "awaiting_phone":
            phone = normalize_phone(text)
            if not phone:
                await event.reply(
                    format_card("Invalid Number", ["Send phone with country code (e.g. <code>+919876543210</code>) or <code>/cancel</code>."]),
                    parse_mode="html",
                )
                return
            if any(acc["phone"] == phone for acc in store["accounts"]):
                await event.reply(
                    format_card("Duplicate Number", [f"<code>{esc(phone)}</code> is already added. Send another or <code>/cancel</code>."]),
                    parse_mode="html",
                )
                return
            if len(store["accounts"]) >= MAX_ACCOUNTS:
                await cleanup_state(event.chat_id)
                await event.reply(
                    format_card("Capacity Reached", [f"Max capacity (<code>{MAX_ACCOUNTS}/{MAX_ACCOUNTS}</code>) reached."]),
                    parse_mode="html",
                )
                return

            status_msg = await event.reply(
                format_card("Dispatching OTP", [f"<code>⠋ Connecting to Telegram for {esc(phone)}...</code>"], icon="◈"),
                parse_mode="html",
            )
            client = create_client(StringSession(), use_abridged=False)
            try:
                await client.connect()
                await safe_edit(
                    status_msg,
                    format_card("Dispatching OTP", [f"<code>⠹ Sending verification code to {esc(phone)}...</code>"], icon="✦"),
                )
                sent = await run_with_retry(lambda: client.send_code_request(phone), retries=3, delay=1.8, client=client)
                user_states[event.chat_id] = {
                    "step": "awaiting_otp",
                    "phone": phone,
                    "phone_code_hash": sent.phone_code_hash,
                    "client": client,
                }
                await safe_edit(
                    status_msg,
                    format_card(
                        "OTP Dispatched",
                        [
                            f"• <b>Number:</b> <code>{esc(phone)}</code>",
                            "• Reply with the OTP code.",
                            "• Tip: send with spaces (e.g. <code>1 2 3 4 5</code>) if Telegram blocks plain codes.",
                        ],
                    ),
                )
            except PhoneNumberInvalidError:
                await safe_disconnect(client)
                await safe_edit(status_msg, format_card("Invalid Number", ["Telegram rejected that phone number. Try again or <code>/cancel</code>."]))
            except FloodWaitError as fw:
                await safe_disconnect(client)
                await safe_edit(status_msg, format_card("Rate Limited", [f"Wait <code>{fw.seconds}s</code> before requesting OTP again."]))
            except Exception as exc:
                await safe_disconnect(client)
                await safe_edit(status_msg, format_card("Error", [f"Failed to send OTP: <code>{esc(exc)}</code>"]))
            return

        # 3. OTP Verification
        if step == "awaiting_otp":
            otp = re.sub(r"\D", "", text)
            if not otp:
                await event.reply(format_card("OTP Required", ["Send the numeric OTP code (or <code>/cancel</code>)."]), parse_mode="html")
                return
            client: TelegramClient = state["client"]
            phone: str = state["phone"]
            verify_msg = await event.reply(
                format_card("Verifying OTP", [f"<code>⠋ Authenticating {esc(phone)}...</code>"], icon="◈"),
                parse_mode="html",
            )
            try:
                await run_with_retry(
                    lambda: client.sign_in(phone=phone, code=otp, phone_code_hash=state["phone_code_hash"]),
                    retries=3,
                    delay=1.8,
                    client=client,
                )
                me = await client.get_me()
                display_name = " ".join(filter(None, [getattr(me, "first_name", ""), getattr(me, "last_name", "")])).strip() or phone
                session_str = client.session.save()

                store = load_store()
                if len(store["accounts"]) >= MAX_ACCOUNTS:
                    await cleanup_state(event.chat_id)
                    await safe_edit(verify_msg, format_card("Capacity Reached", [f"Max <code>{MAX_ACCOUNTS}</code> accounts reached."]))
                    return

                store["accounts"].append(
                    {
                        "phone": phone,
                        "name": display_name,
                        "session": session_str,
                        "dp_type": "custom" if getattr(me, "photo", None) else "default",
                        "channels": [],
                    }
                )
                save_store(store)
                await cleanup_state(event.chat_id)
                await safe_edit(
                    verify_msg,
                    format_card(
                        "Account Verified",
                        [
                            f"• <b>Phone:</b> <code>{esc(phone)}</code>",
                            f"• <b>Name:</b> {esc(display_name)}",
                            f"• <b>Fleet Capacity:</b> <code>{len(store['accounts'])}/{MAX_ACCOUNTS}</code>",
                        ],
                        icon="✔",
                    ),
                )
            except PhoneCodeInvalidError:
                await safe_edit(verify_msg, format_card("Incorrect OTP", ["Wrong OTP code. Please try again (or <code>/cancel</code>)."]))
            except PhoneCodeExpiredError:
                await cleanup_state(event.chat_id)
                await safe_edit(verify_msg, format_card("OTP Expired", ["Code expired. Send <code>/add</code> to request a fresh OTP."]))
            except SessionPasswordNeededError:
                state["step"] = "awaiting_2fa"
                await safe_edit(verify_msg, format_card("2FA Required", ["This account has 2FA enabled. Reply with your 2FA password (or <code>/cancel</code>)."]))
            except Exception as exc:
                await safe_edit(verify_msg, format_card("Sign-In Error", [f"<code>{esc(exc)}</code>\n\nSend OTP again or <code>/cancel</code> to retry."]))
            return

        # 4. 2FA Cloud Password
        if step == "awaiting_2fa":
            client = state["client"]
            phone = state["phone"]
            try:
                await run_with_retry(
                    lambda: client.sign_in(password=text),
                    retries=3,
                    delay=1.8,
                    client=client,
                )
                me = await client.get_me()
                display_name = " ".join(filter(None, [getattr(me, "first_name", ""), getattr(me, "last_name", "")])).strip() or phone
                session_str = client.session.save()

                store = load_store()
                store["accounts"].append(
                    {
                        "phone": phone,
                        "name": display_name,
                        "session": session_str,
                        "dp_type": "custom" if getattr(me, "photo", None) else "default",
                        "channels": [],
                    }
                )
                save_store(store)
                await cleanup_state(event.chat_id)
                await event.reply(
                    format_card(
                        "2FA Verified",
                        [
                            f"• <b>Phone:</b> <code>{esc(phone)}</code>",
                            f"• <b>Name:</b> {esc(display_name)}",
                            f"• <b>Fleet Capacity:</b> <code>{len(store['accounts'])}/{MAX_ACCOUNTS}</code>",
                        ],
                    ),
                    parse_mode="html",
                )
            except Exception as exc:
                await event.reply(format_card("2FA Error", [f"<code>{esc(exc)}</code>"]), parse_mode="html")
            return

        # 5. Global Delay Input
        if step == "awaiting_global_delay":
            parsed = parse_delay_seconds(text)
            if parsed is None:
                await event.reply(
                    format_card("Invalid Delay", ["Send a number/time like <code>0</code>, <code>5</code>, <code>10s</code>, or <code>1m</code> (or <code>/cancel</code>)."]),
                    parse_mode="html",
                )
                return
            store["delay_seconds"] = parsed
            save_store(store)
            await cleanup_state(event.chat_id)
            await event.reply(
                format_card(
                    "Delay Updated",
                    [f"• <b>Default time per account:</b> <code>{esc(format_delay(parsed))}</code>"],
                    icon="✔",
                ),
                parse_mode="html",
            )
            return

        # 6. Random X Names Update
        if step == "awaiting_names":
            names = parse_names(text)
            if not names:
                await event.reply(format_card("No Names Found", ["Send names separated by new lines or commas (or <code>/cancel</code>)."]), parse_mode="html")
                return

            accounts = store["accounts"]
            if not accounts:
                await cleanup_state(event.chat_id)
                await event.reply(format_card("Empty Fleet", ["No accounts added yet."]), parse_mode="html")
                return

            count = min(len(names), len(accounts))
            chosen_indices = random.sample(range(len(accounts)), count)
            random.shuffle(names)
            delay_seconds = float(store.get("delay_seconds", 1.0))

            progress_msg = await event.reply(
                format_card("Renaming Accounts", [f"<code>⠋ Renaming {count} random account(s)...</code>"]),
                parse_mode="html",
            )

            updated_lines: list[str] = []
            errors: list[str] = []
            last_edit = 0.0
            cancelled = False
            cancel_ev = asyncio.Event()
            active_cancels[event.chat_id] = cancel_ev

            try:
                for i, acc_idx in enumerate(chosen_indices):
                    if cancel_ev.is_set():
                        cancelled = True
                        break
                    acc = accounts[acc_idx]
                    now = time.monotonic()
                    if now - last_edit >= 0.7 or i == 0:
                        spinner = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
                        pulse = PULSE_FRAMES[i % len(PULSE_FRAMES)]
                        await safe_edit(
                            progress_msg,
                            format_card(
                                "Renaming Accounts",
                                [
                                    f"<code>{spinner} Updating {esc(acc['phone'])} ({i + 1}/{count})</code>",
                                    f"<code>{render_progress_bar(i, count)}</code>",
                                ],
                                icon=pulse,
                            ),
                        )
                        last_edit = now

                    new_name = names[i][:64]
                    client = create_client(StringSession(acc["session"]))
                    try:
                        await client.connect()
                        if not await client.is_user_authorized():
                            errors.append(f"{acc['phone']}: session expired")
                            continue
                        await client(UpdateProfileRequest(first_name=new_name, last_name=""))
                        old_name = acc.get("name", "")
                        acc["name"] = new_name
                        updated_lines.append(f"<code>{esc(acc['phone'])}</code>: {esc(old_name)} → <b>{esc(new_name)}</b>")
                    except FloodWaitError as fw:
                        errors.append(f"{acc['phone']}: FloodWait {fw.seconds}s")
                    except Exception as exc:
                        errors.append(f"{acc['phone']}: {exc}")
                    finally:
                        await safe_disconnect(client)

                    if i < count - 1 and delay_seconds > 0:
                        if await sleep_with_countdown(progress_msg, "Renaming Accounts", i + 1, count, delay_seconds, cancel_ev):
                            cancelled = True
                            break
            finally:
                active_cancels.pop(event.chat_id, None)
                save_store(store)
                await cleanup_state(event.chat_id)

            lines = [
                f"<code>{render_progress_bar(len(updated_lines), count)}</code>",
                f"• <b>Renamed:</b> <code>{len(updated_lines)}/{len(accounts)}</code>",
                f"• <b>Delay per account:</b> <code>{esc(format_delay(delay_seconds))}</code>",
                "",
                *updated_lines,
            ]
            if cancelled:
                lines.append("• <i>Stopped early via /cancel.</i>")
            if errors:
                lines.append("")
                lines.extend(f"⚠ <code>{esc(e)}</code>" for e in errors[:8])
            await safe_edit(progress_msg, format_card("Names Stopped" if cancelled else "Names Updated", lines))
            return

        # 7. /join Link
        if step == "awaiting_join_link":
            target = parse_join_target(text)
            if not target or target[0] == "chat_id":
                await event.reply(
                    format_card(
                        "Invalid Link",
                        ["Send a valid link like <code>https://t.me/+AbCdEfG</code>, <code>https://t.me/channelname</code>, or <code>@channelname</code>."],
                    ),
                    parse_mode="html",
                )
                return
            user_states[event.chat_id] = {
                "step": "awaiting_join_count",
                "join_type": target[0],
                "join_value": target[1],
                "join_raw": text,
            }
            await event.reply(
                format_card(
                    "Link Accepted",
                    [
                        f"• <b>Target:</b> <code>{esc(text)}</code>",
                        f"• Send number (<b>X</b>) of accounts to join/request (<code>1</code> to <code>{len(store['accounts'])}</code>, or <code>all</code>).",
                    ],
                ),
                parse_mode="html",
            )
            return

        # 8. /join Count (X) -> Ask Time Per Account
        if step == "awaiting_join_count":
            accounts = store["accounts"]
            if not accounts:
                await cleanup_state(event.chat_id)
                await event.reply(format_card("Empty Fleet", ["No accounts added yet."]), parse_mode="html")
                return

            if text.lower() == "all":
                requested_x = len(accounts)
            elif text.isdigit() and int(text) > 0:
                requested_x = int(text)
            else:
                await event.reply(
                    format_card("Invalid Count", [f"Send a number between <code>1</code> and <code>{len(accounts)}</code>, or <code>all</code>."]),
                    parse_mode="html",
                )
                return

            default_delay = float(store.get("delay_seconds", 1.0))
            user_states[event.chat_id] = {
                "step": "awaiting_join_delay",
                "join_type": state["join_type"],
                "join_value": state["join_value"],
                "join_raw": state["join_raw"],
                "requested_x": requested_x,
            }
            await event.reply(
                format_card(
                    "Time Per Account",
                    [
                        f"• <b>Accounts queued:</b> <code>{min(requested_x, len(accounts))}</code>",
                        "• Send delay time between each account request.",
                        "• Examples: <code>0</code>, <code>5</code>, <code>15s</code>, <code>1m</code> (or tap a button below).",
                    ],
                ),
                parse_mode="html",
                buttons=delay_preset_buttons("joindelay", default_delay),
            )
            return

        # 9. /join Delay -> Execute
        if step == "awaiting_join_delay":
            delay_sec = parse_delay_seconds(text)
            if delay_sec is None:
                await event.reply(
                    format_card("Invalid Time", ["Send delay in seconds/minutes like <code>0</code>, <code>5</code>, <code>10s</code>, or <code>1m</code> (or <code>/cancel</code>)."]),
                    parse_mode="html",
                )
                return
            progress_msg = await event.reply(
                format_card(
                    "Joining Target",
                    [f"<code>⠋ Starting {state['requested_x']} account(s) · {format_delay(delay_sec)} delay...</code>"],
                    icon="◈",
                ),
                parse_mode="html",
            )
            summary = await execute_join_batch(
                event.chat_id,
                progress_msg,
                state["join_type"],
                state["join_value"],
                state["join_raw"],
                state["requested_x"],
                delay_sec,
            )
            await safe_edit(progress_msg, summary)
            return

        # 10. /leave Link / Username / Chat ID
        if step == "awaiting_leave_link":
            target = parse_join_target(text)
            if not target:
                await event.reply(
                    format_card(
                        "Invalid Target",
                        ["Send a valid Telegram link (<code>t.me/name</code>, <code>t.me/+hash</code>), <code>@username</code>, or chat ID."],
                    ),
                    parse_mode="html",
                )
                return
            user_states[event.chat_id] = {
                "step": "awaiting_leave_count",
                "leave_type": target[0],
                "leave_value": target[1],
                "leave_raw": text,
            }
            await event.reply(
                format_card(
                    "Leave Target Saved",
                    [
                        f"• <b>Target:</b> <code>{esc(text)}</code>",
                        f"• Send how many accounts (<b>X</b>) should leave (<code>1</code> to <code>{len(store['accounts'])}</code>, or <code>all</code>).",
                    ],
                ),
                parse_mode="html",
            )
            return

        # 11. /leave Count (X or all) -> Ask Time Per Account
        if step == "awaiting_leave_count":
            accounts = store["accounts"]
            if not accounts:
                await cleanup_state(event.chat_id)
                await event.reply(format_card("Empty Fleet", ["No accounts added yet."]), parse_mode="html")
                return

            if text.lower() == "all":
                target_leaves = len(accounts)
            elif text.isdigit() and int(text) > 0:
                target_leaves = min(int(text), len(accounts))
            else:
                await event.reply(
                    format_card("Invalid Count", [f"Send a number between <code>1</code> and <code>{len(accounts)}</code>, or <code>all</code>."]),
                    parse_mode="html",
                )
                return

            default_delay = float(store.get("delay_seconds", 1.0))
            user_states[event.chat_id] = {
                "step": "awaiting_leave_delay",
                "leave_type": state["leave_type"],
                "leave_value": state["leave_value"],
                "leave_raw": state["leave_raw"],
                "target_leaves": target_leaves,
            }
            await event.reply(
                format_card(
                    "Time Per Account",
                    [
                        f"• <b>Accounts to leave:</b> <code>{target_leaves}</code>",
                        "• Send delay time between each leave request.",
                        "• Examples: <code>0</code>, <code>5</code>, <code>15s</code>, <code>1m</code> (or tap a button below).",
                    ],
                ),
                parse_mode="html",
                buttons=delay_preset_buttons("leavedelay", default_delay),
            )
            return

        # 12. /leave Delay -> Execute
        if step == "awaiting_leave_delay":
            delay_sec = parse_delay_seconds(text)
            if delay_sec is None:
                await event.reply(
                    format_card("Invalid Time", ["Send delay in seconds/minutes like <code>0</code>, <code>5</code>, <code>10s</code>, or <code>1m</code> (or <code>/cancel</code>)."]),
                    parse_mode="html",
                )
                return
            progress_msg = await event.reply(
                format_card(
                    "Leaving Channel",
                    [f"<code>⠋ Processing leave · {format_delay(delay_sec)} delay...</code>"],
                    icon="◈",
                ),
                parse_mode="html",
            )
            summary = await execute_leave_batch(
                event.chat_id,
                progress_msg,
                state["leave_type"],
                state["leave_value"],
                state["leave_raw"],
                state["target_leaves"],
                delay_sec,
            )
            await safe_edit(progress_msg, summary)
            return

    await bot.run_until_disconnected()


async def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOTFATHER_TOKEN" or API_ID == 0 or not API_HASH or API_HASH == "YOUR_API_HASH":
        raise SystemExit(
            "Missing credentials. Set BOT_TOKEN, API_ID, and API_HASH environment variables "
            "(or edit the constants at the top of bot.py) before running."
        )

    attempt = 0
    while True:
        attempt += 1
        use_abridged = (attempt % 2 == 0)
        use_memory_session = attempt >= 4
        if attempt == 3:
            print("[TeleFleet] Resetting stale bot session file before retry...")
            _reset_bot_session_files()

        session_target: Any = StringSession() if use_memory_session else "telefleet_bot"
        bot = create_client(session_target, use_abridged=use_abridged)
        try:
            await run_bot_session(bot)
            break
        except FloodWaitError as fw:
            wait_s = int(getattr(fw, "seconds", 10) or 10) + 1
            print(f"[TeleFleet] FloodWait from Telegram: retrying in {wait_s}s...")
            await asyncio.sleep(wait_s)
        except Exception as exc:
            err_name = exc.__class__.__name__
            if err_name in {"AccessTokenExpiredError", "AccessTokenInvalidError", "ApiIdInvalidError"}:
                raise SystemExit(f"Invalid Telegram credentials ({err_name}): {exc}") from exc
            backoff = min(30, 3 * attempt)
            print(
                f"[TeleFleet] Connection/auth error on attempt #{attempt} "
                f"({err_name}: {exc}). Retrying in {backoff}s..."
            )
            await asyncio.sleep(backoff)
        finally:
            await safe_disconnect(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("TeleFleet Pro stopped.")


