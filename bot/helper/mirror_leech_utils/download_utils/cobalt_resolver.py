import asyncio
from os import getenv
from urllib.parse import urlparse

import httpx


COBALT_API_URL = (
    getenv(
        "COBALT_API_URL",
        "http://127.0.0.1:19000/",
    ).rstrip("/")
    + "/"
)


def is_tiktok_url(url: str) -> bool:
    try:
        host = (
            urlparse(str(url)).hostname
            or ""
        ).lower()
    except Exception:
        return False

    return (
        host == "tiktok.com"
        or host.endswith(".tiktok.com")
    )


def _clean_filename(value: object) -> str:
    filename = str(
        value or "tiktok-video.mp4"
    )

    filename = (
        filename
        .replace("/", "_")
        .replace("\\", "_")
        .strip()
    )

    return filename or "tiktok-video.mp4"


async def resolve_tiktok_url(
    url: str,
) -> tuple[str, str]:
    """
    Trả về:
        (download_url, filename)

    Cobalt đôi khi nhận lỗi fetch tạm thời từ TikTok.
    Vì vậy request được retry bằng payload tối giản.
    """

    if not is_tiktok_url(url):
        raise ValueError(
            "URL không thuộc TikTok."
        )

    payloads = [
        # Lần đầu ưu tiên chất lượng tối đa.
        {
            "url": url,
            "downloadMode": "auto",
            "videoQuality": "max",
            "filenameStyle": "basic",
            "allowH265": False,
        },

        # Fallback đã được kiểm chứng hoạt động.
        {
            "url": url,
        },

        # Retry lần cuối cho lỗi TikTok tạm thời.
        {
            "url": url,
        },
    ]

    last_error = "Không có phản hồi từ Cobalt."

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=15.0,
            read=90.0,
            write=30.0,
            pool=15.0,
        ),
        follow_redirects=True,
    ) as client:
        for index, payload in enumerate(
            payloads,
            start=1,
        ):
            try:
                response = await client.post(
                    COBALT_API_URL,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.HTTPError as exc:
                last_error = (
                    f"Lần {index}: lỗi kết nối Cobalt: "
                    f"{type(exc).__name__}: {exc}"
                )
            else:
                try:
                    data = response.json()
                except Exception:
                    data = None

                if isinstance(data, dict):
                    status = str(
                        data.get("status") or ""
                    ).lower()

                    if status in {
                        "tunnel",
                        "redirect",
                    }:
                        download_url = str(
                            data.get("url") or ""
                        ).strip()

                        if download_url:
                            return (
                                download_url,
                                _clean_filename(
                                    data.get("filename")
                                ),
                            )

                        last_error = (
                            f"Lần {index}: Cobalt không "
                            "trả URL tải."
                        )

                    elif status == "picker":
                        raise RuntimeError(
                            "TikTok này là slideshow hoặc "
                            "có nhiều mục; đường tải video "
                            "đơn chưa xử lý dạng này."
                        )

                    elif status == "local-processing":
                        raise RuntimeError(
                            "Cobalt yêu cầu xử lý media "
                            "cục bộ; đường tải hiện tại "
                            "chưa hỗ trợ kết quả này."
                        )

                    elif status == "error":
                        error = data.get("error") or {}

                        if isinstance(error, dict):
                            code = str(
                                error.get("code")
                                or "unknown"
                            )
                            context = error.get(
                                "context"
                            )
                        else:
                            code = str(error)
                            context = None

                        last_error = (
                            f"Lần {index}: {code}"
                        )

                        if context:
                            last_error += (
                                f" | context={context}"
                            )

                    else:
                        last_error = (
                            f"Lần {index}: HTTP "
                            f"{response.status_code}, "
                            f"status={status!r}, "
                            f"body={data!r}"
                        )

                else:
                    body = response.text[:1000]

                    last_error = (
                        f"Lần {index}: HTTP "
                        f"{response.status_code}, "
                        f"body={body!r}"
                    )

            if index < len(payloads):
                await asyncio.sleep(
                    1.5 * index
                )

    raise RuntimeError(
        "Cobalt không lấy được TikTok sau "
        f"{len(payloads)} lần thử. {last_error}"
    )
