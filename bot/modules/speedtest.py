from asyncio import (
    Lock,
    create_subprocess_exec,
    to_thread,
    wait_for,
)
from asyncio.subprocess import PIPE
from html import escape
from json import loads
from os import environ
from time import monotonic
from unicodedata import normalize
from urllib.parse import urlencode
from urllib.request import (
    ProxyHandler,
    Request,
    build_opener,
)

from pyrogram.enums import ParseMode

from ..helper.ext_utils.bot_utils import new_task
from ..helper.telegram_helper.message_utils import (
    delete_message,
    edit_message,
    send_message,
)


speedtest_lock = Lock()

SERVER_SEARCH_API = "https://www.speedtest.net/api/js/servers"

PROXY_VARIABLES = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


def normalize_key(value):
    value = normalize("NFKD", str(value or ""))
    value = value.encode("ascii", errors="ignore").decode()
    return "".join(character for character in value.lower() if character.isalnum())


# Alias -> (từ khóa tìm trên Speedtest, tên hiển thị)
_PRESET_DATA = {
    # Đông Nam Á
    "sg": ("Singapore", "Singapore"),
    "singapore": ("Singapore", "Singapore"),

    # Hong Kong
    "hk": ("Hong Kong", "Hong Kong"),
    "hongkong": ("Hong Kong", "Hong Kong"),

    # Nhật Bản
    "jp": ("Tokyo", "Tokyo, Japan"),
    "japan": ("Tokyo", "Tokyo, Japan"),
    "tokyo": ("Tokyo", "Tokyo, Japan"),

    # Hàn Quốc
    "kr": ("Seoul", "Seoul, South Korea"),
    "korea": ("Seoul", "Seoul, South Korea"),
    "southkorea": ("Seoul", "Seoul, South Korea"),
    "seoul": ("Seoul", "Seoul, South Korea"),

    # Hoa Kỳ bờ Tây
    "us": ("Los Angeles", "Los Angeles, United States"),
    "usa": ("Los Angeles", "Los Angeles, United States"),
    "america": ("Los Angeles", "Los Angeles, United States"),
    "uswest": ("Los Angeles", "Los Angeles, United States"),
    "losangeles": ("Los Angeles", "Los Angeles, United States"),
    "la": ("Los Angeles", "Los Angeles, United States"),

    # Hoa Kỳ bờ Đông
    "useast": ("New York", "New York, United States"),
    "newyork": ("New York", "New York, United States"),
    "ny": ("New York", "New York, United States"),

    # Thành phố Mỹ khác
    "chicago": ("Chicago", "Chicago, United States"),
    "seattle": ("Seattle", "Seattle, United States"),
    "dallas": ("Dallas", "Dallas, United States"),
    "sanfrancisco": ("San Francisco", "San Francisco, United States"),

    # Anh
    "uk": ("London", "London, United Kingdom"),
    "england": ("London", "London, United Kingdom"),
    "unitedkingdom": ("London", "London, United Kingdom"),
    "london": ("London", "London, United Kingdom"),

    # Pháp
    "fr": ("Paris", "Paris, France"),
    "france": ("Paris", "Paris, France"),
    "paris": ("Paris", "Paris, France"),

    # Đức
    "de": ("Frankfurt", "Frankfurt, Germany"),
    "germany": ("Frankfurt", "Frankfurt, Germany"),
    "frankfurt": ("Frankfurt", "Frankfurt, Germany"),

    # Hà Lan
    "nl": ("Amsterdam", "Amsterdam, Netherlands"),
    "netherlands": ("Amsterdam", "Amsterdam, Netherlands"),
    "amsterdam": ("Amsterdam", "Amsterdam, Netherlands"),

    # Úc
    "au": ("Sydney", "Sydney, Australia"),
    "australia": ("Sydney", "Sydney, Australia"),
    "sydney": ("Sydney", "Sydney, Australia"),

    # Canada
    "ca": ("Toronto", "Toronto, Canada"),
    "canada": ("Toronto", "Toronto, Canada"),
    "toronto": ("Toronto", "Toronto, Canada"),

    # UAE
    "uae": ("Dubai", "Dubai, United Arab Emirates"),
    "dubai": ("Dubai", "Dubai, United Arab Emirates"),

    # Ấn Độ
    "india": ("Mumbai", "Mumbai, India"),
    "mumbai": ("Mumbai", "Mumbai, India"),
}

LOCATION_PRESETS = {
    normalize_key(alias): value
    for alias, value in _PRESET_DATA.items()
}

PRESET_HELP = """
🌍 <b>SPEEDTEST LOCATIONS</b>

<b>Asia</b>
<code>/speedtest singapore</code>
<code>/speedtest hongkong</code>
<code>/speedtest tokyo</code>
<code>/speedtest seoul</code>

<b>United States</b>
<code>/speedtest usa</code> — Los Angeles
<code>/speedtest newyork</code>
<code>/speedtest chicago</code>
<code>/speedtest seattle</code>
<code>/speedtest dallas</code>
<code>/speedtest sanfrancisco</code>

<b>Europe</b>
<code>/speedtest london</code>
<code>/speedtest paris</code>
<code>/speedtest frankfurt</code>
<code>/speedtest amsterdam</code>

<b>Other regions</b>
<code>/speedtest sydney</code>
<code>/speedtest toronto</code>
<code>/speedtest dubai</code>
<code>/speedtest mumbai</code>

<b>Other modes</b>
<code>/speedtest</code> — nearest server
<code>/speedtest auto</code> — nearest server
<code>/speedtest 59883</code> — server ID
<code>/speedtest Madrid</code> — custom city search
""".strip()


def format_number(value, digits=2):
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def safe_text(value, default="N/A"):
    value = str(value or "").strip()
    return escape(value) if value else default


def resolve_location(argument):
    key = normalize_key(argument)

    if key in LOCATION_PRESETS:
        return LOCATION_PRESETS[key]

    # Thành phố tùy ý, ví dụ /speedtest Madrid
    cleaned = " ".join(str(argument).split())
    return cleaned, cleaned


def server_score(server, search_term):
    query = normalize_key(search_term)
    city = normalize_key(server.get("name"))
    country = normalize_key(server.get("country"))
    sponsor = normalize_key(server.get("sponsor"))

    score = 0

    if city == query:
        score += 1000
    elif query and query in city:
        score += 700
    elif city and city in query:
        score += 500

    if country == query:
        score += 400
    elif query and query in country:
        score += 250

    if query and query in sponsor:
        score += 100

    # Ưu tiên những bản ghi có đầy đủ thông tin.
    if server.get("id"):
        score += 20
    if server.get("host"):
        score += 10

    return score


def fetch_speedtest_servers(search_term):
    params = urlencode(
        {
            "engine": "js",
            "https_functional": "true",
            "limit": "25",
            "search": search_term,
        }
    )

    request = Request(
        f"{SERVER_SEARCH_API}?{params}",
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 Chrome/130 Safari/537.36"
            ),
        },
    )

    # Không để HTTP_PROXY của bot can thiệp vào bước tìm server.
    opener = build_opener(ProxyHandler({}))

    with opener.open(request, timeout=20) as response:
        raw = response.read().decode("utf-8", errors="replace")

    data = loads(raw)

    if not isinstance(data, list):
        raise RuntimeError("Speedtest server directory returned invalid data.")

    servers = []

    for server in data:
        if not isinstance(server, dict):
            continue

        server_id = str(server.get("id") or "").strip()

        if not server_id.isdigit():
            continue

        servers.append(server)

    servers.sort(
        key=lambda item: server_score(item, search_term),
        reverse=True,
    )

    return servers


def server_description(server):
    city = safe_text(server.get("name"))
    country = safe_text(server.get("country"))
    sponsor = safe_text(server.get("sponsor"))
    server_id = safe_text(server.get("id"))

    return (
        f"<b>{city}, {country}</b>\n"
        f"Sponsor: <b>{sponsor}</b>\n"
        f"Server ID: <code>{server_id}</code>"
    )


async def execute_speedtest(clean_env, server_id=None):
    command = [
        "speedtest",
        "--accept-license",
        "--accept-gdpr",
        "--format=json",
    ]

    if server_id:
        command.append(f"--server-id={server_id}")

    started_at = monotonic()

    try:
        process = await create_subprocess_exec(
            *command,
            stdout=PIPE,
            stderr=PIPE,
            env=clean_env,
        )
    except FileNotFoundError:
        return {
            "returncode": 127,
            "stdout": "",
            "stderr": "Ookla Speedtest CLI was not found in the container.",
            "elapsed": 0,
            "timed_out": False,
        }
    except Exception as error:
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": str(error),
            "elapsed": 0,
            "timed_out": False,
        }

    try:
        stdout, stderr = await wait_for(
            process.communicate(),
            timeout=300,
        )
    except TimeoutError:
        process.kill()
        await process.communicate()

        return {
            "returncode": 124,
            "stdout": "",
            "stderr": "Speedtest timed out after 300 seconds.",
            "elapsed": monotonic() - started_at,
            "timed_out": True,
        }

    return {
        "returncode": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace").strip(),
        "stderr": stderr.decode("utf-8", errors="replace").strip(),
        "elapsed": monotonic() - started_at,
        "timed_out": False,
    }


@new_task
async def run_speedtest(_, message):
    parts = (message.text or "").split(maxsplit=1)
    argument = parts[1].strip() if len(parts) > 1 else ""

    argument_key = normalize_key(argument)

    if argument_key in {
        "help",
        "list",
        "location",
        "locations",
        "preset",
        "presets",
        "server",
        "servers",
    }:
        await send_message(message, PRESET_HELP)
        return

    if speedtest_lock.locked():
        await send_message(
            message,
            "⚠️ Another Speedtest is already running. "
            "Please wait for it to finish.",
        )
        return

    async with speedtest_lock:
        progress = await send_message(
            message,
            "🚀 <b>Running Ookla Speedtest...</b>\n"
            "This process usually takes 30–120 seconds.",
        )

        if isinstance(progress, str):
            return

        clean_env = environ.copy()

        for key in PROXY_VARIABLES:
            clean_env.pop(key, None)

        requested_location = ""
        server_candidates = []
        direct_server_id = None

        if not argument or argument_key in {"auto", "default", "nearest"}:
            await edit_message(
                progress,
                "🚀 <b>Running Ookla Speedtest...</b>\n"
                "Target: <b>Automatic nearest server</b>\n"
                "This process usually takes 30–120 seconds.",
            )

        elif argument.isdigit():
            direct_server_id = argument

            await edit_message(
                progress,
                "🚀 <b>Running Ookla Speedtest...</b>\n"
                f"Target server ID: <code>{escape(argument)}</code>\n"
                "This process usually takes 30–120 seconds.",
            )

        else:
            search_term, requested_location = resolve_location(argument)

            await edit_message(
                progress,
                "🔎 <b>Searching for an Ookla server...</b>\n"
                f"Requested location: <b>{escape(requested_location)}</b>",
            )

            try:
                server_candidates = await to_thread(
                    fetch_speedtest_servers,
                    search_term,
                )
            except Exception as error:
                await edit_message(
                    progress,
                    "❌ <b>Unable to search Speedtest servers.</b>\n"
                    f"<code>{escape(str(error))}</code>\n\n"
                    "Try <code>/speedtest presets</code> or use a numeric "
                    "server ID.",
                )
                return

            if not server_candidates:
                await edit_message(
                    progress,
                    "❌ <b>No matching Speedtest server was found.</b>\n"
                    f"Search: <code>{escape(search_term)}</code>\n\n"
                    "Try another city or use "
                    "<code>/speedtest presets</code>.",
                )
                return

        result = None
        selected_server = None

        if server_candidates:
            # Thử tối đa ba server. Chỉ thử server kế tiếp nếu server trước
            # lỗi nhanh; tránh chạy ba bài Speedtest đầy đủ liên tiếp.
            for index, candidate in enumerate(server_candidates[:3]):
                candidate_id = str(candidate["id"])

                await edit_message(
                    progress,
                    "🚀 <b>Running Ookla Speedtest...</b>\n"
                    f"Requested: <b>{escape(requested_location)}</b>\n"
                    f"Target: {server_description(candidate)}\n"
                    "This process usually takes 30–120 seconds.",
                )

                attempt = await execute_speedtest(
                    clean_env,
                    candidate_id,
                )

                if attempt["returncode"] == 0:
                    result = attempt
                    selected_server = candidate
                    break

                # Lỗi sau thời gian dài thì không tự chạy thêm bài test khác.
                if attempt["timed_out"] or attempt["elapsed"] > 25:
                    result = attempt
                    selected_server = candidate
                    break

                result = attempt
                selected_server = candidate

        else:
            result = await execute_speedtest(
                clean_env,
                direct_server_id,
            )

        if not result or result["returncode"] != 0:
            error_text = (
                result.get("stderr")
                or result.get("stdout")
                or "Unknown error"
            )

            await edit_message(
                progress,
                "❌ <b>Speedtest Failed</b>\n"
                f"<code>{escape(error_text[-1800:])}</code>",
            )
            return

        output = result["stdout"]
        json_start = output.find("{")

        if json_start < 0:
            await edit_message(
                progress,
                "❌ Speedtest returned invalid JSON data.",
            )
            return

        try:
            speed_result = loads(output[json_start:])
        except Exception as error:
            await edit_message(
                progress,
                "❌ <b>Unable to parse the Speedtest result.</b>\n"
                f"<code>{escape(str(error))}</code>",
            )
            return

        if speed_result.get("type") != "result":
            await edit_message(
                progress,
                "❌ Speedtest did not return a complete result.",
            )
            return

        download = speed_result.get("download") or {}
        upload = speed_result.get("upload") or {}
        ping = speed_result.get("ping") or {}
        server = speed_result.get("server") or {}
        result_info = speed_result.get("result") or {}

        # Ookla JSON trả bandwidth theo byte/giây.
        download_bps = float(download.get("bandwidth") or 0)
        upload_bps = float(upload.get("bandwidth") or 0)

        download_mbs = download_bps / (1024 * 1024)
        upload_mbs = upload_bps / (1024 * 1024)

        download_mbps = download_bps * 8 / 1_000_000
        upload_mbps = upload_bps * 8 / 1_000_000

        latency = ping.get("latency")
        jitter = ping.get("jitter")
        packet_loss = speed_result.get("packetLoss")

        packet_loss_text = (
            "N/A"
            if packet_loss is None
            else f"{float(packet_loss):.2f}%"
        )

        result_url = str(result_info.get("url") or "").strip()

        requested_line = ""

        if requested_location:
            requested_line = (
                f"🎯 Requested: <b>{escape(requested_location)}</b>\n\n"
            )

        caption = (
            f"{requested_line}"
            "🚀 <b>SPEEDTEST RESULTS</b>\n"
            f"├ Download: <b>{download_mbs:.2f} MB/s</b>\n"
            f"│  └ {download_mbps:.2f} Mbps\n"
            f"├ Upload: <b>{upload_mbs:.2f} MB/s</b>\n"
            f"│  └ {upload_mbps:.2f} Mbps\n"
            f"├ Ping: <b>{format_number(latency)} ms</b>\n"
            f"├ Jitter: <b>{format_number(jitter)} ms</b>\n"
            f"├ Packet Loss: <b>{packet_loss_text}</b>\n"
            f"└ ISP: <b>{safe_text(speed_result.get('isp'))}</b>\n"
            "\n"
            "📡 <b>SERVER INFORMATION</b>\n"
            f"├ Location: <b>{safe_text(server.get('location'))}</b>\n"
            f"├ Country: <b>{safe_text(server.get('country'))}</b>\n"
            f"├ Sponsor: <b>{safe_text(server.get('name'))}</b>\n"
            f"└ Server ID: <code>{safe_text(server.get('id'))}</code>"
        )

        if result_url:
            image_url = f"{result_url.rstrip('/')}.png"

            try:
                await message.reply_photo(
                    photo=image_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    disable_notification=True,
                )
                await delete_message(progress)
                return
            except Exception:
                caption += (
                    "\n\n"
                    f'🔗 <a href="{escape(result_url)}">'
                    "View Full Speedtest Result</a>"
                )

        await edit_message(progress, caption)
