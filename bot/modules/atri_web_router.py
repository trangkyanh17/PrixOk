from __future__ import annotations

import re
import unicodedata
from typing import Literal


AtriMode = Literal["chat", "web", "tools", "code"]


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "").casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("đ", "d")
    return " ".join(value.split())


TOOLS_PATTERNS = (
    r"\bthoi tiet\b",
    r"\bnhiet do\b",
    r"\bdu bao\b",
    r"\bweather\b",
    r"\bdelta force\b",
    r"三角洲行动",
    r"\byoutube\b",
    r"\btim video\b",
    r"\blink .*nguy hiem\b",
    r"\burl .*nguy hiem\b",
    r"\bphishing\b",
    r"\bmalware\b",
    r"\bsafe browsing\b",
    r"\bkiem tra link\b",
    r"\bgeocode\b",
    r"\btoa do\b",
    r"\bdia chi chuan\b",
    r"\bdich\b",
    r"\btranslate\b",
    r"\bgoogle books\b",
    r"\bocr\b",
    r"\bdoc chu trong anh\b",
    r"\btrich xuat chu\b",
    r"\bdocument ai\b",
    r"\bphan tich pdf\b",
    r"\bphan tich file\b",
    r"\bdoc file\b",
    r"\bdoc tai lieu\b",
    r"\bgoogle sheet\b",
    r"\bgoogle sheets\b",
    r"\bbang tinh\b",
    r"\bisbn\b",
    r"\btim sach\b",
    r"\bgmail\b",
    r"\bemail\b",
    r"\bmail\b",
    r"\bgoogle drive\b",
    r"\bdrive\b",
    r"\bcalendar\b",
    r"\blich cua tao\b",
    r"\blich hom nay\b",
    r"\blich ngay mai\b",
    r"\bnoi bang giong\b",
    r"\btra loi bang giong\b",
    r"\bdoc thanh tieng\b",
    r"\btts\b",
    r"\bgoogle tool\b",
    r"\bgoogle api nao\b",
)

WEB_PATTERNS = (
    r"\btim tren mang\b",
    r"\btim tren web\b",
    r"\btim tren internet\b",
    r"\btra cuu\b",
    r"\bsearch web\b",
    r"\bgoogle search\b",
    r"\bnguon\b",
    r"\bsource\b",
    r"\bkiem chung\b",
    r"\bmoi nhat\b",
    r"\bhien tai\b",
    r"\blatest\b",
    r"\bcurrent\b",
    r"\brecent\b",
    r"\btin tuc\b",
    r"\bnews\b",
    r"\bversion\b",
    r"\bphien ban\b",
    r"\brelease\b",
    r"\bchangelog\b",
    r"\bcve[- ]?\d",
    r"\blo hong\b",
    r"\bdocs\b",
    r"\bdocumentation\b",
    r"\beol\b",
    r"\bend of life\b",
    r"\bho tro den\b",
    r"\bgia hien tai\b",
    r"\bprice\b",
)

FACTUAL_PATTERNS = (
    r"\bla gi\b",
    r"\bai la\b",
    r"\bla ai\b",
    r"\bo dau\b",
    r"\bkhi nao\b",
    r"\bbao gio\b",
    r"\bbao nhieu\b",
    r"\btai sao\b",
    r"\bvi sao\b",
    r"\bco that khong\b",
)

URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def choose_atri_mode(text: str) -> AtriMode:
    normalized = _fold(text)
    if not normalized:
        return "chat"

    for pattern in TOOLS_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return "tools"

    if URL_RE.search(str(text or "")):
        return "web"

    for pattern in WEB_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            return "web"

    return "chat"


# ATRI_EXPLICIT_GITHUB_LOOKUP_HELPER_V1
_GITHUB_LOOKUP_SIGNALS = (
    "tim tren github",
    "tim github",
    "xem tren github",
    "xem github",
    "search github",
    "github search",
    "kiem tra github",
    "check github",
    "tra cuu github",
    "doc tren github",
)


def is_explicit_github_lookup(text: str) -> bool:
    value = _fold(text)
    return (
        "github" in value
        and any(
            signal in value
            for signal in _GITHUB_LOOKUP_SIGNALS
        )
    )


# ATRI_UNIFIED_ROUTE_V3
_atri_choose_mode_original = choose_atri_mode


def choose_atri_mode(text: str) -> str:
    value = str(text or "").casefold().strip()

    # GitHub coding/repository intent must use GitHub MCP, not Google Search.
    github_context = (
        "repo",
        "repository",
        "source",
        "code",
        "commit",
        "branch",
        "issue",
        "pull request",
        "pr ",
        "release",
        "github actions",
    )

    # ATRI_GITHUB_DIRECT_LOOKUP_V2
    if (
        is_explicit_github_lookup(text)
        or (
            "github" in value
            and any(x in value for x in github_context)
        )
    ):
        return "code"

    # Public product/software lifecycle information must use fresh web data.
    lifecycle_value = _fold(text)
    lifecycle_signals = (
        "eol",
        "end of life",
        "het ho tro",
        "ngung ho tro",
        "ho tro toi nam nao",
        "ho tro den nam nao",
        "ho tro toi khi nao",
        "ho tro den khi nao",
        "ho tro bao lau",
        "con duoc ho tro",
        "con ho tro khong",
        "maintenance until",
        "support until",
        "supported until",
        "security updates until",
        "end of support",
    )

    if any(signal in lifecycle_value for signal in lifecycle_signals):
        return "web"

    # 1. Coding/plugin requests always win over web search.
    code_signals = (
        "context7",
        "serena",
        "semgrep",
        "sentry",
        "chrome devtools",
        "chrome-devtools",
        "github mcp",
        "code plugin",
        "code_plugin",
        "mcp tool",
        "mcp plugin",
        "viết code",
        "viet code",
        "sửa code",
        "sua code",
        "fix code",
        "debug",
        "traceback",
        "syntaxerror",
        "typeerror",
        "modulenotfounderror",
        "stack trace",
        "dockerfile",
        "docker compose",
        "docker-compose",
        "requirements.txt",
        "package.json",
        "pip install",
        "npm install",
        "git diff",
        "python",
        "javascript",
        "typescript",
        "golang",
        "rust",
        "c++",
        ".py",
        ".js",
        ".ts",
        ".go",
        ".rs",
        ".cpp",
        "tra docs",
        "tra tài liệu code",
    )

    if any(signal in value for signal in code_signals):
        return "code"

    # 2. Personal Google Workspace.
    direct_workspace = (
        "gmail",
        "google drive",
        "drive của tôi",
        "drive của tao",
        "drive của mình",
        "google calendar",
    )

    if any(signal in value for signal in direct_workspace):
        return "tools"

    calendar_words = (
        "lịch",
        "calendar",
        "cuộc hẹn",
        "meeting",
        "appointment",
    )
    personal_words = (
        "của tôi",
        "của tao",
        "của mình",
        "hôm nay",
        "ngày mai",
        "7 ngày",
        "tuần này",
        "tuần tới",
        "sắp tới",
    )

    if (
        any(x in value for x in calendar_words)
        and any(x in value for x in personal_words)
    ):
        return "tools"

    # 3. Preserve original chat/web routing.
    return _atri_choose_mode_original(text)
