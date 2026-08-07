#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

DEFAULT_DB = Path(
    "/app/atri_data/delta_force_cn_s1_s10.sqlite3"
)
DEFAULT_CATALOG = Path(
    "/app/atri_data/delta_force_cn_seasons.json"
)
DEFAULT_SEED = Path(
    "/app/atri_data/delta_force_cn_seed_entities.jsonl"
)
FALLBACK_CATALOG = Path(
    "/app/data/delta_force_cn_seasons.json"
)
FALLBACK_SEED = Path(
    "/app/data/delta_force_cn_seed_entities.jsonl"
)
DEFAULT_CACHE = Path(
    "/app/atri_data/delta_force_cn_cache"
)

GTIDB_WEAPON_TEMPLATE = (
    "https://gtidb.com/weapons/{weapon_id}"
)
SJZ_INDEX_TEMPLATE = (
    "https://sjz.jbskins.com/item/index/p/{page}.html"
)
SJZ_DETAIL_BASE = "https://sjz.jbskins.com"
SJZ_GUIDE_TEMPLATE = (
    "https://sjz.jbskins.com/guide/detail/id/{guide_id}.html"
)
SJZ_DAMAGE_URL = "https://sjz.jbskins.com/Shanghai"
AMMO_ARMOR_GUIDE_URL = (
    "https://www.taptap.cn/moment/555144387588262453"
)
ORZICE_ALL_ITEMS_URL = (
    "https://orzice.com/workApi/v1/sjz_api/item_info_all"
)

ALLOWED_HOSTS = {
    "www.taptap.cn",
    "taptap.cn",
    "gtidb.com",
    "www.gtidb.com",
    "sjz.jbskins.com",
    "orzice.com",
    "www.orzice.com",
}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 Chrome/131 Safari/537.36"
)


def norm(value: Any) -> str:
    value = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    ).casefold()

    output: list[str] = []
    spaced = False

    for char in value:
        if char.isalnum() or char in ".+-_":
            output.append(char)
            spaced = False
        elif not spaced:
            output.append(" ")
            spaced = True

    return "".join(output).strip()


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.hrefs: list[str] = []
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self._skip = 0
        self._title = 0
        self._h1 = 0

    def handle_starttag(
        self,
        tag: str,
        attrs,
    ) -> None:
        tag = tag.casefold()

        if tag in {
            "script",
            "style",
            "noscript",
            "svg",
        }:
            self._skip += 1

        if tag == "title":
            self._title += 1

        if tag == "h1":
            self._h1 += 1

        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(str(href))

    def handle_endtag(
        self,
        tag: str,
    ) -> None:
        tag = tag.casefold()

        if (
            tag in {
                "script",
                "style",
                "noscript",
                "svg",
            }
            and self._skip
        ):
            self._skip -= 1

        if tag == "title" and self._title:
            self._title -= 1

        if tag == "h1" and self._h1:
            self._h1 -= 1

    def handle_data(
        self,
        data: str,
    ) -> None:
        if self._skip:
            return

        value = re.sub(
            r"\s+",
            " ",
            html.unescape(data),
        ).strip()

        if not value:
            return

        self.lines.append(value)

        if self._title:
            self.title_parts.append(value)

        if self._h1:
            self.h1_parts.append(value)


def parse_visible(
    raw: str,
) -> VisibleTextParser:
    parser = VisibleTextParser()
    parser.feed(raw)
    return parser


def compact_lines(
    lines: Iterable[str],
) -> list[str]:
    output: list[str] = []
    previous = None

    ignored = {
        "下载手机 APP",
        "扫码下载",
        "Android APK 下载",
        "前往论坛",
        "关注",
        "查看",
        "评论",
        "只看作者",
        "最热",
        "分享",
        "举报",
    }

    for line in lines:
        line = re.sub(
            r"\s+",
            " ",
            line,
        ).strip()

        if (
            not line
            or line in ignored
            or line == previous
        ):
            continue

        previous = line
        output.append(line)

    return output


def chunk_text(
    text: str,
    size: int = 2600,
    overlap: int = 220,
) -> list[str]:
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    ).strip()

    if not text:
        return []

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(
            len(text),
            start + size,
        )

        if end < len(text):
            cut = text.rfind(
                "\n",
                start + size // 2,
                end,
            )

            if cut > start:
                end = cut

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = max(
            start + 1,
            end - overlap,
        )

    return chunks


def safe_host(
    url: str,
) -> bool:
    return (
        urlparse(url).hostname or ""
    ).casefold() in ALLOWED_HOSTS


def cache_path(
    cache_dir: Path,
    url: str,
) -> Path:
    digest = hashlib.sha256(
        url.encode()
    ).hexdigest()

    return cache_dir / f"{digest}.html"


def fetch_page(
    client: httpx.Client,
    url: str,
    cache_dir: Path,
) -> tuple[str, str]:
    if not safe_host(url):
        raise ValueError(
            f"Host không được phép: {url}"
        )

    target = cache_path(
        cache_dir,
        url,
    )

    try:
        response = client.get(url)
        response.raise_for_status()

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temp = target.with_suffix(".tmp")
        temp.write_text(
            response.text,
            encoding="utf-8",
        )
        os.replace(temp, target)

        return response.text, "network"

    except Exception:
        if target.is_file():
            return (
                target.read_text(
                    encoding="utf-8"
                ),
                "cache",
            )

        raise


def load_json(
    path: Path,
) -> Any:
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def load_jsonl(
    path: Path,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    with path.open(
        encoding="utf-8"
    ) as handle:
        for line_number, line in enumerate(
            handle,
            1,
        ):
            line = line.strip()

            if not line:
                continue

            try:
                records.append(
                    json.loads(line)
                )
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"JSONL lỗi {path}:"
                    f"{line_number}: {exc}"
                ) from exc

    return records


def source_doc_id(
    season: int,
    url: str,
    chunk_index: int,
) -> str:
    raw = (
        f"{season}|{url}|{chunk_index}"
    ).encode()

    return (
        "doc-"
        + hashlib.sha256(raw).hexdigest()[:24]
    )


def entity_id(
    prefix: str,
    name: str,
    category: str,
) -> str:
    raw = (
        f"{prefix}|{name}|{category}"
    ).encode("utf-8")

    return (
        prefix
        + "-"
        + hashlib.sha256(raw).hexdigest()[:20]
    )


def first_number(
    value: str,
) -> int | None:
    found = re.search(
        r"-?\d[\d,]*",
        value or "",
    )

    if not found:
        return None

    try:
        return int(
            found.group(0).replace(
                ",",
                "",
            )
        )
    except ValueError:
        return None


def first_float(
    value: str,
) -> float | None:
    found = re.search(
        r"-?\d+(?:\.\d+)?",
        value or "",
    )

    if not found:
        return None

    try:
        return float(
            found.group(0)
        )
    except ValueError:
        return None


CATEGORY_MAP = {
    "护甲": "armor",
    "头盔": "helmet",
    "胸挂": "chest_rig",
    "背包": "backpack",
    "子弹": "ammo",
    "药品": "consumable",
    "医疗": "consumable",
    "钥匙": "key",
    "收藏品": "collectible",
    "瞄具": "attachment",
    "枪托": "attachment",
    "前握把": "attachment",
    "后握把": "attachment",
    "护木": "attachment",
    "枪管": "attachment",
    "弹匣": "attachment",
    "枪口": "attachment",
    "功能性配件": "attachment",
    "武器": "weapon",
    "突击步枪": "weapon",
    "战斗步枪": "weapon",
    "冲锋枪": "weapon",
    "轻机枪": "weapon",
    "精确射手步枪": "weapon",
    "狙击步枪": "weapon",
    "霰弹枪": "weapon",
    "手枪": "weapon",
    "近战武器": "weapon",
    "战术道具": "gear",
}



from urllib.parse import urljoin


class AnchorCaptureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[
            tuple[str, str, dict[str, str]]
        ] = []
        self.images: list[dict[str, str]] = []
        self._in_anchor = False
        self._href = ""
        self._attrs: dict[str, str] = {}
        self._parts: list[str] = []

    def _flush_anchor(self) -> None:
        if not self._in_anchor:
            return

        text = re.sub(
            r"\s+",
            " ",
            " ".join(self._parts),
        ).strip()

        self.anchors.append(
            (
                self._href,
                text,
                self._attrs,
            )
        )

        self._in_anchor = False
        self._href = ""
        self._attrs = {}
        self._parts = []

    def handle_starttag(
        self,
        tag: str,
        attrs,
    ) -> None:
        tag = tag.casefold()
        attrs_dict = {
            str(key): str(value)
            for key, value in attrs
            if value is not None
        }

        if tag == "a":
            self._flush_anchor()
            self._in_anchor = True
            self._href = attrs_dict.get(
                "href",
                "",
            )
            self._attrs = attrs_dict
            self._parts = []
            return

        if tag == "img":
            self.images.append(attrs_dict)

            if self._in_anchor:
                alt = re.sub(
                    r"\s+",
                    " ",
                    attrs_dict.get(
                        "alt",
                        "",
                    ),
                ).strip()

                if alt:
                    self._parts.append(alt)

    def handle_startendtag(
        self,
        tag: str,
        attrs,
    ) -> None:
        self.handle_starttag(
            tag,
            attrs,
        )

    def handle_endtag(
        self,
        tag: str,
    ) -> None:
        if (
            tag.casefold() == "a"
            and self._in_anchor
        ):
            self._flush_anchor()

    def handle_data(
        self,
        data: str,
    ) -> None:
        if not self._in_anchor:
            return

        value = re.sub(
            r"\s+",
            " ",
            html.unescape(data),
        ).strip()

        if value:
            self._parts.append(value)

    def close(self) -> None:
        super().close()
        self._flush_anchor()

def next_value(
    lines: list[str],
    label: str,
    *,
    max_ahead: int = 4,
) -> str:
    label_n = norm(label)

    for index, line in enumerate(lines):
        if norm(line) != label_n:
            continue

        for value in lines[
            index + 1:index + 1 + max_ahead
        ]:
            if norm(value) and norm(value) != label_n:
                return value.strip()

    return ""


def numeric_or_text(value: str) -> Any:
    value = str(value or "").strip()

    if not value:
        return None

    compact = value.replace(",", "")

    if re.fullmatch(r"-?\d+", compact):
        return int(compact)

    if re.fullmatch(r"-?\d+(?:\.\d+)?", compact):
        return float(compact)

    number = first_float(value)

    if number is not None and re.fullmatch(
        r"-?\d+(?:\.\d+)?(?:m/s|m|秒|kg|发/分|发)?",
        compact,
        re.I,
    ):
        return number

    return value


def category_key_from_cn(value: str) -> str:
    value_n = norm(value)

    mapping = {
        "步枪": "weapon",
        "突击步枪": "weapon",
        "战斗步枪": "weapon",
        "冲锋枪": "weapon",
        "轻机枪": "weapon",
        "精确射手步枪": "weapon",
        "狙击步枪": "weapon",
        "霰弹枪": "weapon",
        "手枪": "weapon",
        "近战武器": "weapon",
        "子弹": "ammo",
        "弹药": "ammo",
        "护甲": "armor",
        "头盔": "helmet",
        "胸挂": "chest_rig",
        "背包": "backpack",
        "消耗品": "consumable",
        "医疗": "consumable",
        "药品": "consumable",
        "钥匙": "key",
        "收集品": "collectible",
        "收藏品": "collectible",
        "枪管": "attachment",
        "枪口": "attachment",
        "前握把": "attachment",
        "后握把": "attachment",
        "枪托": "attachment",
        "护木": "attachment",
        "弹匣": "attachment",
        "瞄具": "attachment",
        "瞄准镜": "attachment",
        "功能性配件": "attachment",
        "功能配件": "attachment",
        "战术道具": "gear",
    }

    for cn_name, category in mapping.items():
        if norm(cn_name) == value_n:
            return category

    return "item"


def parse_gtidb_weapon(
    raw: str,
    url: str,
    snapshot: str,
) -> dict[str, Any] | None:
    parser = parse_visible(raw)
    lines = compact_lines(parser.lines)

    if not any(
        norm(line) == norm("基础伤害")
        for line in lines
    ):
        return None

    name = (
        next_value(lines, "名称")
        or " ".join(parser.h1_parts).strip()
    )

    if not name:
        return None

    weapon_type = next_value(lines, "类型")

    field_map = {
        "基础伤害": "flesh_damage",
        "护甲伤害": "armor_damage",
        "射速": "rate_of_fire",
        "射程": "range_m",
        "后坐力控制": "recoil_control",
        "操控速度": "handling",
        "据枪稳定性": "stability",
        "腰射精度": "hipfire_accuracy",
        "弹容量": "magazine_capacity",
        "开火模式": "fire_mode_cn",
        "子弹初速": "muzzle_velocity_mps",
        "枪声传播距离": "gunshot_distance_m",
    }

    stats: dict[str, Any] = {
        "mode_scope": "cn_database_unspecified",
    }

    for label, key in field_map.items():
        value = next_value(lines, label)

        if value:
            stats[key] = numeric_or_text(value)

    description = ""

    if parser.h1_parts:
        h1_text = " ".join(parser.h1_parts).strip()

        try:
            h1_index = lines.index(h1_text)
        except ValueError:
            h1_index = -1

        if h1_index >= 0:
            for candidate in lines[
                h1_index + 1:h1_index + 8
            ]:
                if norm(candidate) in {
                    norm("名称"),
                    norm("类型"),
                    norm("基础伤害"),
                }:
                    continue

                if len(candidate) >= 8:
                    description = candidate
                    break

    caliber_match = re.search(
        (
            r"("
            r"\.\d+\s*(?:Lapua\s*Magnum|ACP)?"
            r"|"
            r"\d+(?:\.\d+)?[x×]\d+(?:\.\d+)?"
            r"(?:mm)?R?"
            r")"
        ),
        description,
        re.I,
    )

    if caliber_match:
        stats["caliber"] = caliber_match.group(1)

    weapon_id = url.rstrip("/").rsplit("/", 1)[-1]

    return {
        "id": f"gtidb-weapon-{weapon_id}",
        "name_cn": name,
        "name_en": "",
        "name_vi": "",
        "aliases": [],
        "category": "weapon",
        "subcategory": weapon_type,
        "mode": ["unspecified"],
        "platform": ["pc", "mobile"],
        "region": "cn",
        "season_introduced": None,
        "season_last_seen": 10,
        "grade": None,
        "stats": stats,
        "description": description,
        "source_url": url,
        "source_type": "cn_community_database",
        "confidence": "community_single_source",
        "snapshot_at": snapshot,
        "historical_safe": False,
    }


GENERIC_ANCHOR_TEXT = {
    "",
    "查看",
    "查看详情",
    "详情",
    "收藏",
    "分享",
}


def parse_sjz_index(
    raw: str,
    source_url: str,
    snapshot: str,
) -> tuple[
    list[dict[str, Any]],
    list[str],
]:
    anchor_parser = AnchorCaptureParser()
    anchor_parser.feed(raw)
    anchor_parser.close()

    records_by_id: dict[
        str,
        dict[str, Any],
    ] = {}
    detail_urls: set[str] = set()

    row_pattern = re.compile(
        r"^(?P<name>.+?)\s+"
        r"(?P<category>\S+)\s+"
        r"(?P<price>¥\s*[\d,]+|价格待定)\s+"
        r"(?P<grade>\d+)级(?:道具)?\s+"
        r"(?P<weight>\d+(?:\.\d+)?)\s+"
        r"(?P<width>\d+)\s*[×x]\s*"
        r"(?P<height>\d+)$",
        re.I,
    )

    for href, text, attrs in anchor_parser.anchors:
        match = re.search(
            (
                r"/item/detail/id/"
                r"(\d+)\.html"
                r"(?:[?#].*)?$"
            ),
            href,
            re.I,
        )

        if not match:
            continue

        detail_id = match.group(1)
        detail_url = urljoin(
            SJZ_DETAIL_BASE,
            href,
        )
        detail_urls.add(detail_url)

        clean = re.sub(
            r"\s+",
            " ",
            html.unescape(text),
        ).strip()

        clean = re.sub(
            r"\s*查看详情\s*$",
            "",
            clean,
        ).strip()

        parsed = row_pattern.match(clean)

        if not parsed:
            fallback_name = re.sub(
                r"\s+",
                " ",
                (
                    attrs.get("title", "")
                    or attrs.get(
                        "aria-label",
                        "",
                    )
                ),
            ).strip()

            if not fallback_name:
                continue

            records_by_id[detail_id] = {
                "id": f"sjz-item-{detail_id}",
                "name_cn": fallback_name,
                "name_en": "",
                "name_vi": "",
                "aliases": [],
                "category": "item",
                "subcategory": "",
                "mode": ["operations"],
                "platform": ["pc", "mobile"],
                "region": "cn",
                "season_introduced": None,
                "season_last_seen": 10,
                "grade": None,
                "stats": {
                    "market_price_haf_coin_cn": None,
                    "grid_size": None,
                    "weight_kg": None,
                    "detail_id": int(detail_id),
                    "index_source": source_url,
                    "raw_index_text": clean,
                },
                "description": "",
                "source_url": detail_url,
                "source_type": (
                    "cn_community_database"
                ),
                "confidence": (
                    "community_single_source"
                ),
                "snapshot_at": snapshot,
                "historical_safe": False,
            }
            continue

        data = parsed.groupdict()

        price_text = data["price"]
        price = None

        if price_text.startswith("¥"):
            price = int(
                re.sub(
                    r"[^\d]",
                    "",
                    price_text,
                )
            )

        category_cn = data["category"]
        category = category_key_from_cn(
            category_cn
        )

        records_by_id[detail_id] = {
            "id": f"sjz-item-{detail_id}",
            "name_cn": data["name"].strip(),
            "name_en": "",
            "name_vi": "",
            "aliases": [],
            "category": category,
            "subcategory": category_cn,
            "mode": ["operations"],
            "platform": ["pc", "mobile"],
            "region": "cn",
            "season_introduced": None,
            "season_last_seen": 10,
            "grade": int(data["grade"]),
            "stats": {
                "market_price_haf_coin_cn": (
                    price
                ),
                "grid_size": (
                    f"{data['width']}x"
                    f"{data['height']}"
                ),
                "weight_kg": float(
                    data["weight"]
                ),
                "detail_id": int(detail_id),
                "index_source": source_url,
                "raw_index_text": clean,
            },
            "description": "",
            "source_url": detail_url,
            "source_type": (
                "cn_community_database"
            ),
            "confidence": (
                "community_single_source"
            ),
            "snapshot_at": snapshot,
            "historical_safe": False,
        }

    return (
        list(records_by_id.values()),
        sorted(detail_urls),
    )


DETAIL_FIELD_MAP = {
    "重量": "weight_kg",
    "尺寸": "grid_size",
    "价值": "base_value",
    "堆叠数量": "stack_size",
    "伤害": "damage",
    "基础伤害": "flesh_damage",
    "肉体伤害": "flesh_damage",
    "血伤": "flesh_damage",
    "护甲伤害": "armor_damage",
    "甲伤": "armor_damage",
    "射速": "rate_of_fire",
    "精准度": "accuracy",
    "稳定性": "stability",
    "有效射程": "effective_range",
    "射程": "range",
    "弹夹容量": "magazine_capacity",
    "弹容量": "magazine_capacity",
    "射击模式": "fire_mode_cn",
    "开火模式": "fire_mode_cn",
    "口径": "caliber",
    "子弹初速": "muzzle_velocity",
    "防护等级": "protection_grade",
    "耐久度": "durability",
    "移动速度": "movement_speed",
    "操控速度": "handling",
    "冷却时间": "cooldown",
    "治疗量": "heal_amount",
    "回复量": "heal_amount",
    "恢复量": "heal_amount",
    "使用时间": "use_time",
    "持续时间": "duration",
    "穿透等级": "penetration_level",
    "护甲穿透": "penetration_level",
    "兼容性": "compatibility",
    "安装时间": "install_time",
    "控制速度": "control_speed",
}


def parse_sjz_detail(
    raw: str,
    url: str,
    snapshot: str,
) -> dict[str, Any] | None:
    parser = parse_visible(raw)
    lines = compact_lines(parser.lines)

    name = " ".join(
        parser.h1_parts
    ).strip()

    if not name:
        title = " ".join(
            parser.title_parts
        ).strip()

        name = re.sub(
            r"\s*-\s*三角洲.*$",
            "",
            title,
        ).strip()

    if not name:
        return None

    detail_match = re.search(
        r"/id/(\d+)\.html",
        url,
        re.I,
    )

    if not detail_match:
        return None

    detail_id = detail_match.group(1)

    category_cn = ""
    grade = None

    for index, line in enumerate(lines):
        if norm(line) != norm(name):
            continue

        nearby = " ".join(
            lines[
                index + 1:index + 5
            ]
        )

        match = re.search(
            r"([^|\s]+)\s*\|\s*([0-9]+)级道具",
            nearby,
        )

        if match:
            category_cn = match.group(1)
            grade = int(match.group(2))
            break

    if not category_cn:
        for value in lines[:20]:
            if category_key_from_cn(value) != "item":
                category_cn = value
                break

    if grade is None:
        for value in lines[:30]:
            match = re.fullmatch(
                r"([0-9]+)级(?:道具)?",
                value,
            )

            if match:
                grade = int(match.group(1))
                break

    stats: dict[str, Any] = {
        "detail_id": int(detail_id),
    }

    for label, key in DETAIL_FIELD_MAP.items():
        value = next_value(
            lines,
            label,
            max_ahead=2,
        )

        if value:
            stats[key] = numeric_or_text(value)

    market_price = None

    for value in lines[:30]:
        price_match = re.search(
            r"¥\s*([\d,]+)",
            value,
        )

        if price_match:
            market_price = int(
                price_match.group(1).replace(
                    ",",
                    "",
                )
            )
            break

    if market_price is not None:
        stats["market_price_haf_coin_cn"] = (
            market_price
        )

    description = ""

    for index, value in enumerate(lines):
        if norm(value) != norm("道具描述"):
            continue

        for candidate in lines[
            index + 1:index + 6
        ]:
            if (
                candidate
                and not candidate.startswith("暂无")
                and norm(candidate)
                not in {
                    norm("使用建议"),
                    norm("获取信息"),
                }
            ):
                description = candidate
                break

        break

    stats["description"] = description

    category = category_key_from_cn(
        category_cn
    )

    return {
        "id": f"sjz-item-{detail_id}",
        "name_cn": name,
        "name_en": "",
        "name_vi": "",
        "aliases": [],
        "category": category,
        "subcategory": category_cn,
        "mode": ["operations"],
        "platform": ["pc", "mobile"],
        "region": "cn",
        "season_introduced": None,
        "season_last_seen": 10,
        "grade": grade,
        "stats": stats,
        "description": description,
        "source_url": url,
        "source_type": "cn_community_database",
        "confidence": "community_single_source",
        "snapshot_at": snapshot,
        "historical_safe": False,
    }


def parse_operator_guide(
    raw: str,
    url: str,
    snapshot: str,
) -> tuple[
    dict[str, Any] | None,
    str,
    str,
]:
    parser = parse_visible(raw)
    lines = compact_lines(parser.lines)
    title = (
        " ".join(parser.h1_parts).strip()
        or " ".join(parser.title_parts).strip()
    )
    content = "\n".join(lines)

    if (
        "干员" not in title
        and "干员" not in content[:1200]
    ):
        return None, title, content

    patterns = [
        r"干员([^技能介绍详解怎么样\s]+)",
        r"三角洲行动([^技能介绍详解怎么样\s]+)技能",
        r"《三角洲行动》干员([^介绍\s]+)",
    ]

    operator_name = ""

    for pattern in patterns:
        match = re.search(
            pattern,
            title,
        )

        if match:
            operator_name = match.group(1).strip()
            break

    if not operator_name:
        for line in lines[:20]:
            if (
                1 <= len(line) <= 12
                and "干员" not in line
                and "三角洲" not in line
            ):
                operator_name = line
                break

    class_cn = ""

    class_match = re.search(
        r"(?:定位|兵种)[:：]\s*([^\s，。；]+)",
        content,
    )

    if class_match:
        class_cn = class_match.group(1)

    skill_lines = [
        line
        for line in lines
        if re.match(
            (
                r"^(?:[一二三四五六七八九十]+、"
                r"|[0-9]+[、.]"
                r"|被动技能[:：]"
                r"|战术装备[:：]"
                r"|战术道具[:：]"
                r"|干员特长[:：]"
                r"|大招[:：])"
            ),
            line,
        )
    ]

    guide_id = (
        re.search(
            r"/id/(\d+)\.html",
            url,
            re.I,
        )
        or re.search(
            r"/id/(\d+)",
            url,
            re.I,
        )
    )

    record_id = (
        guide_id.group(1)
        if guide_id
        else hashlib.sha256(
            url.encode()
        ).hexdigest()[:12]
    )

    entity = {
        "id": f"sjz-operator-guide-{record_id}",
        "name_cn": operator_name or title,
        "name_en": "",
        "name_vi": "",
        "aliases": [],
        "category": "operator",
        "subcategory": class_cn,
        "mode": ["operations", "warfare"],
        "platform": ["pc", "mobile"],
        "region": "cn",
        "season_introduced": None,
        "season_last_seen": 10,
        "grade": None,
        "stats": {
            "class_cn": class_cn or None,
            "skill_headings": skill_lines[:12],
            "guide_excerpt": content[:4000],
        },
        "description": content[:800],
        "source_url": url,
        "source_type": "cn_community_guide",
        "confidence": "community_single_source",
        "snapshot_at": snapshot,
        "historical_safe": False,
    }

    return entity, title, content


def parse_orzice_items(
    payload: Any,
    snapshot: str,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    raw_items = payload.get("data")

    if not isinstance(raw_items, list):
        return []

    category_map = {
        "gun": "weapon",
        "weapon": "weapon",
        "ammo": "ammo",
        "armor": "armor",
        "helmet": "helmet",
        "chest_rig": "chest_rig",
        "backpack": "backpack",
        "consume": "consumable",
        "consumable": "consumable",
        "key": "key",
        "collection": "collectible",
        "collectible": "collectible",
        "accessory": "attachment",
        "attachment": "attachment",
    }

    records: list[dict[str, Any]] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        name = str(
            item.get("objectName")
            or item.get("name")
            or ""
        ).strip()

        if not name:
            continue

        primary = norm(
            item.get("primaryClass")
        )

        category = category_map.get(
            primary,
            primary or "item",
        )

        detail = item.get("detail")

        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except json.JSONDecodeError:
                detail = {
                    "raw_detail": detail,
                }

        if not isinstance(detail, dict):
            detail = {}

        object_id = (
            item.get("objectID")
            or item.get("oid")
            or item.get("id")
        )

        records.append(
            {
                "id": f"orzice-item-{object_id}",
                "name_cn": name,
                "name_en": "",
                "name_vi": "",
                "aliases": [],
                "category": category,
                "subcategory": str(
                    item.get("secondClass")
                    or ""
                ),
                "mode": ["operations"],
                "platform": ["pc", "mobile"],
                "region": "cn",
                "season_introduced": None,
                "season_last_seen": 10,
                "grade": item.get("grade"),
                "stats": {
                    **detail,
                    "object_id": object_id,
                    "tradeable": item.get("is_get"),
                    "grid_width": item.get("width"),
                    "grid_length": item.get("length"),
                },
                "description": str(
                    item.get("desc")
                    or ""
                ),
                "source_url": ORZICE_ALL_ITEMS_URL,
                "source_type": "cn_community_api",
                "confidence": "community_single_source",
                "snapshot_at": snapshot,
                "historical_safe": False,
            }
        )

    return records


def is_priority_detail(
    record: dict[str, Any],
) -> bool:
    if record.get("category") in {
        "weapon",
        "ammo",
        "consumable",
        "armor",
        "helmet",
        "chest_rig",
        "backpack",
    }:
        return True

    name = str(
        record.get("name_cn")
        or ""
    )

    keywords = (
        "弹",
        "药",
        "医疗",
        "急救",
        "注射",
        "兴奋剂",
        "护甲",
        "头盔",
        "胸挂",
        "背包",
        "步枪",
        "冲锋枪",
        "机枪",
        "狙击",
        "手枪",
        "霰弹枪",
    )

    return any(
        keyword in name
        for keyword in keywords
    )

def merge_entities(
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    ranks = {
        "verified": 4,
        "cross_checked": 3,
        "community_single_source": 2,
        "unverified": 1,
    }

    by_id: dict[str, dict[str, Any]] = {}
    by_name_category: dict[
        tuple[str, str],
        str,
    ] = {}

    for record in records:
        key = (
            norm(
                record.get("name_cn")
                or record.get("name_en")
            ),
            norm(record.get("category")),
        )

        existing_id = (
            by_name_category.get(key)
        )

        record_id = str(
            record.get("id")
            or entity_id(
                "auto",
                key[0],
                key[1],
            )
        )

        if existing_id:
            existing = by_id[existing_id]

            if ranks.get(
                str(record.get("confidence")),
                0,
            ) > ranks.get(
                str(existing.get("confidence")),
                0,
            ):
                preserved_stats = (
                    existing.get("stats")
                    or {}
                )
                record_stats = (
                    record.get("stats")
                    or {}
                )
                record["stats"] = {
                    **preserved_stats,
                    **record_stats,
                }
                record["id"] = existing_id
                by_id[existing_id] = record
            else:
                existing["stats"] = {
                    **(
                        existing.get("stats")
                        or {}
                    ),
                    **(
                        record.get("stats")
                        or {}
                    ),
                }
                existing["aliases"] = sorted(
                    set(
                        (
                            existing.get("aliases")
                            or []
                        )
                        + (
                            record.get("aliases")
                            or []
                        )
                    )
                )

            continue

        record["id"] = record_id
        by_id[record_id] = record
        by_name_category[key] = record_id

    return list(by_id.values())


def build_db(
    db_path: Path,
    catalog: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    metadata: dict[str, str],
) -> None:
    db_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = db_path.with_suffix(
        ".tmp.sqlite3"
    )
    temp.unlink(missing_ok=True)

    with sqlite3.connect(temp) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;

            CREATE TABLE seasons (
                season INTEGER PRIMARY KEY,
                name_cn TEXT NOT NULL,
                name_vi TEXT NOT NULL,
                name_en TEXT NOT NULL,
                release_date TEXT NOT NULL,
                summary_vi TEXT NOT NULL,
                highlights_json TEXT NOT NULL,
                sources_json TEXT NOT NULL
            );

            CREATE TABLE entities (
                id TEXT PRIMARY KEY,
                name_cn TEXT NOT NULL,
                name_en TEXT NOT NULL,
                name_vi TEXT NOT NULL,
                name_key TEXT NOT NULL,
                aliases_json TEXT NOT NULL,
                aliases_key TEXT NOT NULL,
                category TEXT NOT NULL,
                category_key TEXT NOT NULL,
                subcategory TEXT NOT NULL,
                mode_json TEXT NOT NULL,
                mode_key TEXT NOT NULL,
                platform_json TEXT NOT NULL,
                platform_key TEXT NOT NULL,
                region TEXT NOT NULL,
                season_introduced INTEGER,
                season_last_seen INTEGER,
                grade INTEGER,
                stats_json TEXT NOT NULL,
                description TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_type TEXT NOT NULL,
                confidence TEXT NOT NULL,
                snapshot_at TEXT NOT NULL,
                historical_safe INTEGER NOT NULL,
                search_text TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE entities_fts
            USING fts5(
                id UNINDEXED,
                names,
                aliases,
                category,
                description,
                stats,
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE TABLE documents (
                id TEXT PRIMARY KEY,
                season INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_url TEXT NOT NULL,
                source_type TEXT NOT NULL,
                confidence TEXT NOT NULL,
                published_date TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                search_text TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE documents_fts
            USING fts5(
                id UNINDEXED,
                title,
                content,
                tokenize='unicode61 remove_diacritics 2'
            );

            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        for season in catalog:
            connection.execute(
                """
                INSERT INTO seasons
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    season["season"],
                    season["name_cn"],
                    season["name_vi"],
                    season["name_en"],
                    season["release_date"],
                    season["summary_vi"],
                    json.dumps(
                        season.get("highlights")
                        or [],
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        season.get("sources")
                        or [],
                        ensure_ascii=False,
                    ),
                ),
            )

        for record in entities:
            names = [
                record.get("name_cn", ""),
                record.get("name_en", ""),
                record.get("name_vi", ""),
            ]

            aliases = [
                str(value)
                for value in (
                    record.get("aliases")
                    or []
                )
                if str(value).strip()
            ]

            modes = [
                norm(value)
                for value in (
                    record.get("mode")
                    or []
                )
            ]

            platforms = [
                norm(value)
                for value in (
                    record.get("platform")
                    or []
                )
            ]

            stats = (
                record.get("stats")
                or {}
            )

            description = str(
                record.get("description")
                or ""
            )

            search_text = norm(
                " ".join(
                    names
                    + aliases
                    + [
                        str(
                            record.get("category")
                            or ""
                        ),
                        str(
                            record.get("subcategory")
                            or ""
                        ),
                        description,
                        json.dumps(
                            stats,
                            ensure_ascii=False,
                        ),
                    ]
                )
            )

            values = (
                str(record["id"]),
                str(record.get("name_cn") or ""),
                str(record.get("name_en") or ""),
                str(record.get("name_vi") or ""),
                norm(" ".join(names)),
                json.dumps(
                    aliases,
                    ensure_ascii=False,
                ),
                (
                    "|"
                    + "|".join(
                        norm(value)
                        for value in aliases
                    )
                    + "|"
                ),
                str(
                    record.get("category")
                    or "item"
                ),
                norm(
                    record.get("category")
                    or "item"
                ),
                str(
                    record.get("subcategory")
                    or ""
                ),
                json.dumps(
                    record.get("mode")
                    or [],
                    ensure_ascii=False,
                ),
                (
                    "|"
                    + "|".join(modes)
                    + "|"
                ),
                json.dumps(
                    record.get("platform")
                    or [],
                    ensure_ascii=False,
                ),
                (
                    "|"
                    + "|".join(platforms)
                    + "|"
                ),
                "cn",
                record.get("season_introduced"),
                record.get("season_last_seen"),
                record.get("grade"),
                json.dumps(
                    stats,
                    ensure_ascii=False,
                ),
                description,
                str(
                    record.get("source_url")
                    or ""
                ),
                str(
                    record.get("source_type")
                    or ""
                ),
                str(
                    record.get("confidence")
                    or "unverified"
                ),
                str(
                    record.get("snapshot_at")
                    or ""
                ),
                (
                    1
                    if record.get(
                        "historical_safe"
                    )
                    else 0
                ),
                search_text,
            )

            connection.execute(
                """
                INSERT INTO entities
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                values,
            )

            connection.execute(
                """
                INSERT INTO entities_fts(
                    id,
                    names,
                    aliases,
                    category,
                    description,
                    stats
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    " ".join(names),
                    " ".join(aliases),
                    record.get(
                        "category",
                        "",
                    ),
                    description,
                    json.dumps(
                        stats,
                        ensure_ascii=False,
                    ),
                ),
            )

        seen_documents: set[str] = set()

        for document in documents:
            if document["id"] in seen_documents:
                continue

            seen_documents.add(
                document["id"]
            )

            search_text = norm(
                document["title"]
                + " "
                + document["content"]
            )

            connection.execute(
                """
                INSERT INTO documents
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document["id"],
                    document["season"],
                    document["title"],
                    document["content"],
                    document["source_url"],
                    document["source_type"],
                    document["confidence"],
                    document.get(
                        "published_date"
                    )
                    or "",
                    document["chunk_index"],
                    search_text,
                ),
            )

            connection.execute(
                """
                INSERT INTO documents_fts(
                    id,
                    title,
                    content
                )
                VALUES (?, ?, ?)
                """,
                (
                    document["id"],
                    document["title"],
                    document["content"],
                ),
            )

        connection.executemany(
            """
            INSERT INTO metadata(
                key,
                value
            )
            VALUES (?, ?)
            """,
            metadata.items(),
        )

        connection.commit()
        connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)"
        )

    os.replace(
        temp,
        db_path,
    )




def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
    )
    parser.add_argument(
        "--seed",
        type=Path,
        default=DEFAULT_SEED,
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_CACHE,
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
    )
    parser.add_argument(
        "--weapon-id-start",
        type=int,
        default=10000,
    )
    parser.add_argument(
        "--weapon-id-end",
        type=int,
        default=10120,
    )
    parser.add_argument(
        "--sjz-pages",
        type=int,
        default=73,
    )
    parser.add_argument(
        "--detail-limit",
        type=int,
        default=0,
        help=(
            "0 = lấy tất cả detail thuộc nhóm ưu tiên "
            "weapon/ammo/medical/armor."
        ),
    )
    parser.add_argument(
        "--guide-id-end",
        type=int,
        default=80,
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.10,
    )

    arguments = parser.parse_args()

    catalog_path = (
        arguments.catalog
        if arguments.catalog.is_file()
        else FALLBACK_CATALOG
    )

    if not catalog_path.is_file():
        print(
            "DELTA_FORCE_CN_CATALOG_MISSING",
            file=sys.stderr,
        )
        return 1

    catalog = load_json(catalog_path)

    seed_entities: list[dict[str, Any]] = []

    if arguments.seed.is_file():
        seed_entities = load_jsonl(
            arguments.seed
        )
    elif FALLBACK_SEED.is_file():
        seed_entities = load_jsonl(
            FALLBACK_SEED
        )

    # Cấm tái nhập seed cũ liên quan Trek.
    seed_entities = [
        record
        for record in seed_entities
        if "trek" not in norm(
            " ".join(
                [
                    str(
                        record.get("name_cn")
                        or ""
                    ),
                    str(
                        record.get("name_en")
                        or ""
                    ),
                    " ".join(
                        record.get("aliases")
                        or []
                    ),
                ]
            )
        )
    ]

    snapshot = date.today().isoformat()
    documents: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = list(
        seed_entities
    )
    warnings: list[str] = []

    live_official_pages = 0
    weapon_entities = 0
    sjz_index_pages = 0
    sjz_index_entities = 0
    sjz_detail_pages = 0
    operator_documents = 0
    mechanics_documents = 0
    orzice_entities = 0

    for season in catalog:
        source = (
            season.get("sources")
            or [{}]
        )[0]

        summary = str(
            season.get("summary_vi")
            or ""
        )

        content = (
            summary
            + "\nHighlights: "
            + ", ".join(
                season.get("highlights")
                or []
            )
        )

        documents.append(
            {
                "id": source_doc_id(
                    season["season"],
                    source.get("url", ""),
                    0,
                ),
                "season": season["season"],
                "title": (
                    f"S{season['season']} "
                    f"{season['name_cn']} — "
                    "curated official-source summary"
                ),
                "content": content,
                "source_url": source.get(
                    "url",
                    "",
                ),
                "source_type": (
                    "curated_from_official_cn_sources"
                ),
                "confidence": "verified_summary",
                "published_date": season[
                    "release_date"
                ],
                "chunk_index": 0,
            }
        )

    if not arguments.no_network:
        transport = httpx.HTTPTransport(
            retries=3,
        )

        timeout = httpx.Timeout(
            50.0,
            connect=25.0,
        )

        limits = httpx.Limits(
            max_connections=1,
            max_keepalive_connections=1,
        )

        with httpx.Client(
            transport=transport,
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            http1=True,
            http2=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/json;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": (
                    "zh-CN,zh;q=0.9"
                ),
                "Connection": "keep-alive",
            },
        ) as client:
            # 1) Official CN season pages.
            for season in catalog:
                for source in (
                    season.get("sources")
                    or []
                ):
                    try:
                        raw, origin = fetch_page(
                            client,
                            source["url"],
                            arguments.cache,
                        )

                        parsed = parse_visible(raw)
                        lines = compact_lines(
                            parsed.lines
                        )

                        title = (
                            " ".join(
                                parsed.h1_parts
                            ).strip()
                            or source["title"]
                        )

                        content = "\n".join(lines)

                        if len(content) < 120:
                            content = (
                                season["summary_vi"]
                                + "\n"
                                + content
                            )

                        for (
                            chunk_index,
                            chunk,
                        ) in enumerate(
                            chunk_text(content),
                            1,
                        ):
                            documents.append(
                                {
                                    "id": source_doc_id(
                                        season[
                                            "season"
                                        ],
                                        source["url"],
                                        chunk_index,
                                    ),
                                    "season": season[
                                        "season"
                                    ],
                                    "title": title,
                                    "content": chunk,
                                    "source_url": (
                                        source["url"]
                                    ),
                                    "source_type": (
                                        source.get(
                                            "type",
                                            "official_taptap",
                                        )
                                        + (
                                            "_cache"
                                            if origin
                                            == "cache"
                                            else ""
                                        )
                                    ),
                                    "confidence": (
                                        "official_cn_source"
                                    ),
                                    "published_date": (
                                        season[
                                            "release_date"
                                        ]
                                    ),
                                    "chunk_index": (
                                        chunk_index
                                    ),
                                }
                            )

                        live_official_pages += 1

                    except Exception as exc:
                        warnings.append(
                            "OFFICIAL_FETCH_FAILED "
                            f"season={season['season']} "
                            f"url={source['url']} "
                            f"type={type(exc).__name__}"
                        )

            # 2) Official ammo/armor mechanics.
            try:
                raw, origin = fetch_page(
                    client,
                    AMMO_ARMOR_GUIDE_URL,
                    arguments.cache,
                )

                parsed = parse_visible(raw)
                content = "\n".join(
                    compact_lines(parsed.lines)
                )
                title = (
                    " ".join(
                        parsed.h1_parts
                    ).strip()
                    or "子弹与护甲机制"
                )

                for chunk_index, chunk in enumerate(
                    chunk_text(content),
                    1,
                ):
                    documents.append(
                        {
                            "id": source_doc_id(
                                1,
                                AMMO_ARMOR_GUIDE_URL,
                                chunk_index,
                            ),
                            "season": 1,
                            "title": title,
                            "content": chunk,
                            "source_url": (
                                AMMO_ARMOR_GUIDE_URL
                            ),
                            "source_type": (
                                "official_taptap"
                                + (
                                    "_cache"
                                    if origin == "cache"
                                    else ""
                                )
                            ),
                            "confidence": (
                                "official_cn_source"
                            ),
                            "published_date": (
                                "2024-06-27"
                            ),
                            "chunk_index": chunk_index,
                        }
                    )

                mechanics_documents += 1

            except Exception as exc:
                warnings.append(
                    "AMMO_ARMOR_GUIDE_FAILED "
                    f"type={type(exc).__name__}"
                )

            # 3) Structured weapon stats from GTI DB.
            for weapon_id in range(
                arguments.weapon_id_start,
                arguments.weapon_id_end + 1,
            ):
                url = GTIDB_WEAPON_TEMPLATE.format(
                    weapon_id=weapon_id
                )

                try:
                    raw, _ = fetch_page(
                        client,
                        url,
                        arguments.cache,
                    )

                    record = parse_gtidb_weapon(
                        raw,
                        url,
                        snapshot,
                    )

                    if record:
                        entities.append(record)
                        weapon_entities += 1

                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in {
                        404,
                        410,
                    }:
                        warnings.append(
                            "GTIDB_FETCH_FAILED "
                            f"id={weapon_id} "
                            f"status={exc.response.status_code}"
                        )

                except Exception as exc:
                    warnings.append(
                        "GTIDB_FETCH_FAILED "
                        f"id={weapon_id} "
                        f"type={type(exc).__name__}"
                    )

                if arguments.delay > 0:
                    time.sleep(
                        arguments.delay
                    )

            # 4) 869-item index from 三角洲零号站.
            detail_map: dict[
                str,
                dict[str, Any],
            ] = {}

            for page in range(
                1,
                max(1, arguments.sjz_pages) + 1,
            ):
                url = SJZ_INDEX_TEMPLATE.format(
                    page=page
                )

                try:
                    raw, origin = fetch_page(
                        client,
                        url,
                        arguments.cache,
                    )

                    (
                        page_records,
                        detail_urls,
                    ) = parse_sjz_index(
                        raw,
                        url,
                        snapshot,
                    )

                    entities.extend(page_records)
                    sjz_index_entities += len(
                        page_records
                    )
                    sjz_index_pages += 1

                    for record in page_records:
                        detail_map[
                            record["source_url"]
                        ] = record

                    parsed = parse_visible(raw)
                    content = "\n".join(
                        compact_lines(parsed.lines)
                    )

                    for (
                        chunk_index,
                        chunk,
                    ) in enumerate(
                        chunk_text(content),
                        1,
                    ):
                        documents.append(
                            {
                                "id": source_doc_id(
                                    10,
                                    url,
                                    chunk_index,
                                ),
                                "season": 10,
                                "title": (
                                    "三角洲零号站 "
                                    f"道具列表 第{page}页"
                                ),
                                "content": chunk,
                                "source_url": url,
                                "source_type": (
                                    "cn_community_database"
                                    + (
                                        "_cache"
                                        if origin == "cache"
                                        else ""
                                    )
                                ),
                                "confidence": (
                                    "community_single_source"
                                ),
                                "published_date": (
                                    snapshot
                                ),
                                "chunk_index": (
                                    chunk_index
                                ),
                            }
                        )

                except Exception as exc:
                    warnings.append(
                        "SJZ_INDEX_FETCH_FAILED "
                        f"page={page} "
                        f"type={type(exc).__name__}"
                    )

                if arguments.delay > 0:
                    time.sleep(
                        arguments.delay
                    )

            priority_urls = [
                url
                for url, record in detail_map.items()
                if is_priority_detail(record)
            ]

            if arguments.detail_limit > 0:
                priority_urls = priority_urls[
                    :arguments.detail_limit
                ]

            # 5) Detail pages only for guns/ammo/medical/armor.
            for position, url in enumerate(
                priority_urls,
                1,
            ):
                try:
                    raw, _ = fetch_page(
                        client,
                        url,
                        arguments.cache,
                    )

                    record = parse_sjz_detail(
                        raw,
                        url,
                        snapshot,
                    )

                    if record:
                        entities.append(record)
                        sjz_detail_pages += 1

                except Exception as exc:
                    warnings.append(
                        "SJZ_DETAIL_FETCH_FAILED "
                        f"pos={position} "
                        f"url={url} "
                        f"type={type(exc).__name__}"
                    )

                if arguments.delay > 0:
                    time.sleep(
                        max(
                            arguments.delay,
                            0.20,
                        )
                    )

            # 6) Operator guides.
            for guide_id in range(
                1,
                max(1, arguments.guide_id_end) + 1,
            ):
                url = SJZ_GUIDE_TEMPLATE.format(
                    guide_id=guide_id
                )

                try:
                    raw, origin = fetch_page(
                        client,
                        url,
                        arguments.cache,
                    )

                    (
                        entity,
                        title,
                        content,
                    ) = parse_operator_guide(
                        raw,
                        url,
                        snapshot,
                    )

                    if not entity:
                        continue

                    entities.append(entity)
                    operator_documents += 1

                    for (
                        chunk_index,
                        chunk,
                    ) in enumerate(
                        chunk_text(content),
                        1,
                    ):
                        documents.append(
                            {
                                "id": source_doc_id(
                                    10,
                                    url,
                                    chunk_index,
                                ),
                                "season": 10,
                                "title": title,
                                "content": chunk,
                                "source_url": url,
                                "source_type": (
                                    "cn_community_guide"
                                    + (
                                        "_cache"
                                        if origin == "cache"
                                        else ""
                                    )
                                ),
                                "confidence": (
                                    "community_single_source"
                                ),
                                "published_date": snapshot,
                                "chunk_index": (
                                    chunk_index
                                ),
                            }
                        )

                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in {
                        404,
                        410,
                    }:
                        warnings.append(
                            "OPERATOR_GUIDE_FAILED "
                            f"id={guide_id} "
                            f"status={exc.response.status_code}"
                        )

                except Exception as exc:
                    warnings.append(
                        "OPERATOR_GUIDE_FAILED "
                        f"id={guide_id} "
                        f"type={type(exc).__name__}"
                    )

                if arguments.delay > 0:
                    time.sleep(
                        arguments.delay
                    )

            # 7) Damage/health mechanics.
            try:
                raw, origin = fetch_page(
                    client,
                    SJZ_DAMAGE_URL,
                    arguments.cache,
                )

                parsed = parse_visible(raw)
                content = "\n".join(
                    compact_lines(parsed.lines)
                )

                for (
                    chunk_index,
                    chunk,
                ) in enumerate(
                    chunk_text(content),
                    1,
                ):
                    documents.append(
                        {
                            "id": source_doc_id(
                                10,
                                SJZ_DAMAGE_URL,
                                chunk_index,
                            ),
                            "season": 10,
                            "title": (
                                "三角洲行动伤害与护甲计算规则"
                            ),
                            "content": chunk,
                            "source_url": (
                                SJZ_DAMAGE_URL
                            ),
                            "source_type": (
                                "cn_community_mechanics"
                                + (
                                    "_cache"
                                    if origin == "cache"
                                    else ""
                                )
                            ),
                            "confidence": (
                                "community_single_source"
                            ),
                            "published_date": snapshot,
                            "chunk_index": (
                                chunk_index
                            ),
                        }
                    )

                mechanics_documents += 1

            except Exception as exc:
                warnings.append(
                    "DAMAGE_MECHANICS_FAILED "
                    f"type={type(exc).__name__}"
                )

            # 8) Optional all-item API. Không phải điều kiện deploy.
            try:
                response = client.get(
                    ORZICE_ALL_ITEMS_URL
                )
                response.raise_for_status()

                api_records = parse_orzice_items(
                    response.json(),
                    snapshot,
                )

                if api_records:
                    entities.extend(api_records)
                    orzice_entities = len(
                        api_records
                    )

            except Exception as exc:
                warnings.append(
                    "ORZICE_OPTIONAL_FAILED "
                    f"type={type(exc).__name__}"
                )

    entities = merge_entities(entities)

    category_counts: dict[str, int] = {}

    for record in entities:
        category = str(
            record.get("category")
            or "item"
        )
        category_counts[category] = (
            category_counts.get(category, 0)
            + 1
        )

    metadata = {
        "region": "cn",
        "season_min": "1",
        "season_max": "10",
        "current_season": "10",
        "snapshot_date": snapshot,
        "generated_at_utc": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        "season_count": str(
            len(catalog)
        ),
        "entity_count": str(
            len(entities)
        ),
        "document_chunk_count": str(
            len(documents)
        ),
        "live_official_pages": str(
            live_official_pages
        ),
        "weapon_entities": str(
            weapon_entities
        ),
        "sjz_index_pages": str(
            sjz_index_pages
        ),
        "sjz_index_entities": str(
            sjz_index_entities
        ),
        "sjz_detail_pages": str(
            sjz_detail_pages
        ),
        "operator_documents": str(
            operator_documents
        ),
        "mechanics_documents": str(
            mechanics_documents
        ),
        "orzice_entities": str(
            orzice_entities
        ),
        "category_counts_json": json.dumps(
            category_counts,
            ensure_ascii=False,
            sort_keys=True,
        ),
        "data_policy": (
            "CN_ONLY_NO_GLOBAL_NO_GARENA_NO_TREK_SEED"
        ),
        "source_policy": (
            "TAPTAP_OFFICIAL_GTIDB_SJZ_OPTIONAL_ORZICE"
        ),
    }

    build_db(
        arguments.db,
        catalog,
        entities,
        documents,
        metadata,
    )

    strict_failures: list[str] = []

    if len(catalog) != 10:
        strict_failures.append(
            f"seasons={len(catalog)} expected=10"
        )

    if (
        not arguments.no_network
        and live_official_pages < 20
    ):
        strict_failures.append(
            "live_official_pages="
            f"{live_official_pages} minimum=20"
        )

    if (
        not arguments.no_network
        and weapon_entities < 35
    ):
        strict_failures.append(
            "weapon_entities="
            f"{weapon_entities} minimum=35"
        )

    if (
        not arguments.no_network
        and sjz_index_pages < 60
    ):
        strict_failures.append(
            "sjz_index_pages="
            f"{sjz_index_pages} minimum=60"
        )

    if (
        not arguments.no_network
        and sjz_index_entities < 500
    ):
        strict_failures.append(
            "sjz_index_entities="
            f"{sjz_index_entities} minimum=500"
        )

    if (
        not arguments.no_network
        and operator_documents < 3
    ):
        strict_failures.append(
            "operator_documents="
            f"{operator_documents} minimum=3"
        )

    if (
        not arguments.no_network
        and mechanics_documents < 1
    ):
        strict_failures.append(
            "mechanics_documents="
            f"{mechanics_documents} minimum=1"
        )

    report = {
        **metadata,
        "warnings": warnings,
        "strict_failures": strict_failures,
        "db": str(arguments.db),
    }

    report_path = arguments.db.with_suffix(
        ".report.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"DELTA_FORCE_CN_DB={arguments.db}"
    )
    print(
        "DELTA_FORCE_CN_SEASONS="
        f"{len(catalog)}"
    )
    print(
        "DELTA_FORCE_CN_ENTITIES="
        f"{len(entities)}"
    )
    print(
        "DELTA_FORCE_CN_DOCUMENT_CHUNKS="
        f"{len(documents)}"
    )
    print(
        "DELTA_FORCE_CN_LIVE_OFFICIAL_PAGES="
        f"{live_official_pages}"
    )
    print(
        "DELTA_FORCE_CN_WEAPONS="
        f"{weapon_entities}"
    )
    print(
        "DELTA_FORCE_CN_SJZ_INDEX_PAGES="
        f"{sjz_index_pages}"
    )
    print(
        "DELTA_FORCE_CN_SJZ_INDEX_ENTITIES="
        f"{sjz_index_entities}"
    )
    print(
        "DELTA_FORCE_CN_SJZ_DETAIL_PAGES="
        f"{sjz_detail_pages}"
    )
    print(
        "DELTA_FORCE_CN_OPERATOR_DOCS="
        f"{operator_documents}"
    )
    print(
        "DELTA_FORCE_CN_MECHANICS_DOCS="
        f"{mechanics_documents}"
    )
    print(
        "DELTA_FORCE_CN_ORZICE_ENTITIES="
        f"{orzice_entities}"
    )
    print(
        "DELTA_FORCE_CN_CATEGORY_COUNTS="
        + json.dumps(
            category_counts,
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    for warning in warnings[:80]:
        print(
            warning,
            file=sys.stderr,
        )

    if (
        strict_failures
        and not arguments.allow_partial
    ):
        for failure in strict_failures:
            print(
                f"STRICT_SYNC_FAILED={failure}",
                file=sys.stderr,
            )

        print(
            "PARTIAL_DB_PRESERVED="
            f"{arguments.db}",
            file=sys.stderr,
        )
        return 2

    print("DELTA_FORCE_CN_SYNC_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
