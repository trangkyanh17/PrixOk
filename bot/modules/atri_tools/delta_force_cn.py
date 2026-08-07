from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

DB_PATH = Path(
    os.getenv(
        "ATRI_DELTA_FORCE_CN_DB",
        "/app/atri_data/delta_force_cn_s1_s10.sqlite3",
    )
)

SEARCH_DELTA_FORCE_CN_DECLARATION: dict[str, Any] = {
    "name": "search_delta_force_cn",
    "description": (
        "Tra cứu knowledge base Delta Force bản Trung Quốc từ S1 đến S10. "
        "Bắt buộc dùng trước khi trả lời về vũ khí, đạn, giáp, bản đồ, "
        "operator, phương tiện, vật phẩm, mùa hoặc thay đổi cân bằng."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Tên hoặc nội dung cần tra cứu.",
            },
            "season": {
                "type": "integer",
                "description": (
                    "Mùa CN từ 1 đến 10. Bỏ trống để dùng hiện hành."
                ),
            },
            "category": {
                "type": "string",
                "description": (
                    "weapon, ammo, armor, helmet, map, operator, vehicle, "
                    "attachment, gear, key, collectible, consumable, "
                    "season hoặc balance."
                ),
            },
            "mode": {
                "type": "string",
                "description": "operations hoặc warfare.",
            },
            "platform": {
                "type": "string",
                "description": "pc hoặc mobile.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 12,
            },
        },
        "required": ["query"],
    },
}

GET_DELTA_FORCE_CN_HISTORY_DECLARATION: dict[str, Any] = {
    "name": "get_delta_force_cn_history",
    "description": (
        "Tìm lịch sử một thực thể hoặc chủ đề trong tài liệu Delta Force "
        "China S1-S10. Dùng khi hỏi xuất hiện từ mùa nào, từng bị chỉnh ra "
        "sao hoặc lịch sử qua các mùa."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "season_from": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
            },
            "season_to": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    },
}

COMPARE_DELTA_FORCE_CN_SEASONS_DECLARATION: dict[str, Any] = {
    "name": "compare_delta_force_cn_seasons",
    "description": (
        "Lấy bằng chứng cùng một chủ đề ở hai mùa Delta Force China. "
        "Không tự suy ra thay đổi nếu nguồn không ghi rõ."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "season_a": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
            },
            "season_b": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query", "season_a", "season_b"],
    },
}

DELTA_FORCE_CN_TOOL_DECLARATIONS = [
    SEARCH_DELTA_FORCE_CN_DECLARATION,
    GET_DELTA_FORCE_CN_HISTORY_DECLARATION,
    COMPARE_DELTA_FORCE_CN_SEASONS_DECLARATION,
]
DELTA_FORCE_CN_TOOL_NAMES = {
    declaration["name"] for declaration in DELTA_FORCE_CN_TOOL_DECLARATIONS
}


def _norm(value: Any) -> str:
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


def _json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _connect() -> sqlite3.Connection:
    if not DB_PATH.is_file():
        raise FileNotFoundError(
            "Knowledge base Delta Force China chưa tồn tại: "
            f"{DB_PATH}"
        )

    connection = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def _metadata(
    connection: sqlite3.Connection,
) -> dict[str, str]:
    return {
        row["key"]: row["value"]
        for row in connection.execute(
            "SELECT key, value FROM metadata"
        )
    }


def _entity_result(
    row: sqlite3.Row,
) -> dict[str, Any]:
    return {
        "kind": "entity",
        "id": row["id"],
        "name_cn": row["name_cn"],
        "name_en": row["name_en"],
        "name_vi": row["name_vi"],
        "aliases": _json(row["aliases_json"], []),
        "category": row["category"],
        "subcategory": row["subcategory"],
        "mode": _json(row["mode_json"], []),
        "platform": _json(row["platform_json"], []),
        "region": "cn",
        "season_introduced": row["season_introduced"],
        "season_last_seen": row["season_last_seen"],
        "grade": row["grade"],
        "stats": _json(row["stats_json"], {}),
        "source_url": row["source_url"],
        "source_type": row["source_type"],
        "confidence": row["confidence"],
        "snapshot_at": row["snapshot_at"],
    }


def _document_result(
    row: sqlite3.Row,
) -> dict[str, Any]:
    return {
        "kind": "source_document",
        "id": row["id"],
        "season": row["season"],
        "title": row["title"],
        "excerpt": row["content"][:2600],
        "source_url": row["source_url"],
        "source_type": row["source_type"],
        "confidence": row["confidence"],
        "published_date": row["published_date"],
        "chunk_index": row["chunk_index"],
    }


def _search_documents(
    connection: sqlite3.Connection,
    query: str,
    *,
    season: int | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    query_n = _norm(query)
    filters: list[str] = []
    params: list[Any] = []

    if season is not None:
        filters.append("d.season = ?")
        params.append(season)

    where = " AND ".join(filters)
    where = f"AND {where}" if where else ""

    rows: list[sqlite3.Row] = []
    tokens = [
        token
        for token in query_n.split()
        if len(token) >= 1
    ]

    if tokens:
        expression = " OR ".join(
            '"' + token.replace('"', "") + '"*'
            for token in tokens
        )

        try:
            rows = connection.execute(
                f"""
                SELECT d.*
                FROM documents_fts f
                JOIN documents d ON d.id = f.id
                WHERE documents_fts MATCH ? {where}
                ORDER BY
                    bm25(documents_fts),
                    d.season DESC,
                    d.chunk_index
                LIMIT ?
                """,
                [expression, *params, limit],
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []

    if not rows:
        like = f"%{query_n}%"
        rows = connection.execute(
            f"""
            SELECT d.*
            FROM documents d
            WHERE d.search_text LIKE ? {where}
            ORDER BY
                d.season DESC,
                d.chunk_index
            LIMIT ?
            """,
            [like, *params, limit],
        ).fetchall()

    return [
        _document_result(row)
        for row in rows
    ]


def search_delta_force_cn(
    query: str,
    *,
    season: int | None = None,
    category: str = "",
    mode: str = "",
    platform: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    query = str(query or "").strip()

    if not query:
        return {
            "ok": False,
            "error": "Thiếu nội dung tra cứu.",
            "region": "cn",
        }

    try:
        if season is not None:
            season = int(season)
            if not 1 <= season <= 10:
                raise ValueError

        limit = max(
            1,
            min(12, int(limit or 8)),
        )
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "Season hoặc limit không hợp lệ.",
            "region": "cn",
        }

    try:
        connection = _connect()
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "region": "cn",
        }

    with connection:
        meta = _metadata(connection)
        query_n = _norm(query)
        entity_results: list[dict[str, Any]] = []

        current_scope = season in (None, 10)

        if current_scope:
            filters = ["region = 'cn'"]
            params: list[Any] = []

            if category:
                filters.append("category_key = ?")
                params.append(_norm(category))

            if mode:
                filters.append("mode_key LIKE ?")
                params.append(
                    f"%|{_norm(mode)}|%"
                )

            if platform:
                filters.append("platform_key LIKE ?")
                params.append(
                    f"%|{_norm(platform)}|%"
                )

            where = " AND ".join(filters)

            rows = connection.execute(
                f"""
                SELECT *
                FROM entities
                WHERE {where}
                  AND (
                    name_key LIKE ?
                    OR aliases_key LIKE ?
                    OR search_text LIKE ?
                  )
                ORDER BY
                  CASE confidence
                    WHEN 'verified' THEN 0
                    WHEN 'cross_checked' THEN 1
                    ELSE 2
                  END,
                  name_cn
                LIMIT ?
                """,
                [
                    *params,
                    f"%{query_n}%",
                    f"%|{query_n}|%",
                    f"%{query_n}%",
                    limit,
                ],
            ).fetchall()

            entity_results = [
                _entity_result(row)
                for row in rows
            ]

        doc_limit = max(
            1,
            limit - len(entity_results),
        )
        document_results = _search_documents(
            connection,
            query,
            season=season,
            limit=doc_limit,
        )

        results = [
            *entity_results,
            *document_results,
        ][:limit]

    return {
        "ok": True,
        "region": "cn",
        "season_scope": (
            season
            if season is not None
            else "current_cn_s10_plus_history_docs"
        ),
        "query": query,
        "result_count": len(results),
        "results": results,
        "coverage": meta,
        "strict_note": (
            "Không được dùng entity current để điền ngược chỉ số "
            "cho S1-S9. Không có bằng chứng trong results thì phải "
            "nói chưa xác minh."
        ),
    }


def get_delta_force_cn_history(
    query: str,
    *,
    season_from: int = 1,
    season_to: int = 10,
    limit: int = 16,
) -> dict[str, Any]:
    query = str(query or "").strip()

    try:
        season_from = max(
            1,
            min(10, int(season_from or 1)),
        )
        season_to = max(
            1,
            min(10, int(season_to or 10)),
        )
        limit = max(
            1,
            min(20, int(limit or 16)),
        )
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "Tham số lịch sử không hợp lệ.",
            "region": "cn",
        }

    if season_from > season_to:
        season_from, season_to = (
            season_to,
            season_from,
        )

    try:
        connection = _connect()
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "region": "cn",
        }

    with connection:
        all_results: list[dict[str, Any]] = []
        per_season = max(
            1,
            min(4, limit),
        )

        for current_season in range(
            season_from,
            season_to + 1,
        ):
            found = _search_documents(
                connection,
                query,
                season=current_season,
                limit=per_season,
            )
            all_results.extend(found)

            if len(all_results) >= limit:
                break

        meta = _metadata(connection)

    grouped: dict[str, list[dict[str, Any]]] = {}

    for item in all_results[:limit]:
        grouped.setdefault(
            str(item["season"]),
            [],
        ).append(item)

    return {
        "ok": True,
        "region": "cn",
        "query": query,
        "season_from": season_from,
        "season_to": season_to,
        "results_by_season": grouped,
        "result_count": sum(
            len(value)
            for value in grouped.values()
        ),
        "coverage": meta,
        "strict_note": (
            "Mùa không có kết quả phải được coi là chưa có bằng chứng, "
            "không phải chắc chắn chưa tồn tại."
        ),
    }


def compare_delta_force_cn_seasons(
    query: str,
    season_a: int,
    season_b: int,
    *,
    limit: int = 5,
) -> dict[str, Any]:
    try:
        season_a = int(season_a)
        season_b = int(season_b)

        if not (
            1 <= season_a <= 10
            and 1 <= season_b <= 10
        ):
            raise ValueError

        limit = max(
            1,
            min(10, int(limit or 5)),
        )
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": "Season so sánh không hợp lệ.",
            "region": "cn",
        }

    try:
        connection = _connect()
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "region": "cn",
        }

    with connection:
        side_a = _search_documents(
            connection,
            query,
            season=season_a,
            limit=limit,
        )
        side_b = _search_documents(
            connection,
            query,
            season=season_b,
            limit=limit,
        )
        meta = _metadata(connection)

    return {
        "ok": True,
        "region": "cn",
        "query": query,
        "season_a": {
            "season": season_a,
            "evidence": side_a,
        },
        "season_b": {
            "season": season_b,
            "evidence": side_b,
        },
        "coverage": meta,
        "strict_note": (
            "Đây là bằng chứng hai phía, không phải diff đã được "
            "chứng minh. Chỉ kết luận thay đổi khi tài liệu ghi rõ "
            "giá trị hoặc cơ chế cũ và mới."
        ),
    }


async def execute_delta_force_cn_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    arguments = arguments or {}

    if name == "search_delta_force_cn":
        return search_delta_force_cn(
            arguments.get("query", ""),
            season=arguments.get("season"),
            category=arguments.get(
                "category",
                "",
            ),
            mode=arguments.get("mode", ""),
            platform=arguments.get(
                "platform",
                "",
            ),
            limit=arguments.get("limit", 8),
        )

    if name == "get_delta_force_cn_history":
        return get_delta_force_cn_history(
            arguments.get("query", ""),
            season_from=arguments.get(
                "season_from",
                1,
            ),
            season_to=arguments.get(
                "season_to",
                10,
            ),
            limit=arguments.get("limit", 16),
        )

    if name == "compare_delta_force_cn_seasons":
        return compare_delta_force_cn_seasons(
            arguments.get("query", ""),
            arguments.get("season_a"),
            arguments.get("season_b"),
            limit=arguments.get("limit", 5),
        )

    return {
        "ok": False,
        "error": f"Không hỗ trợ công cụ: {name}",
        "region": "cn",
    }
