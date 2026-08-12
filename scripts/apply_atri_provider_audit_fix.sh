#!/usr/bin/env bash
set -Eeuo pipefail

# Standalone deployer for the ATRI provider runtime/audit fix.
# When the runtime files are not beside this script, it downloads them from
# the immutable, GitHub-verified source commit below.

REPOSITORY="trangkyanh17/PrixOk"
SOURCE_REF="${ATRI_FIX_SOURCE_REF:-fd362ba99dbd4104bb82dabd91f7e818b81f197d}"
RAW_BASE="${ATRI_FIX_RAW_BASE:-https://raw.githubusercontent.com/${REPOSITORY}/${SOURCE_REF}}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_SOURCE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BOT_ROOT="${1:-}"

FILES=(
    bot/modules/atri_provider_config.py
    bot/modules/atri_provider_request.py
    bot/modules/atri_provider_capabilities.py
    bot/modules/atri_provider_control.py
    bot/modules/atri_free_pool.py
)

is_bot_root() {
    [[ -f "$1/bot/modules/atri_free_pool.py" ]]
}

detect_bot_root() {
    local candidate
    local candidates=(
        "${PWD}"
        "/app"
        "${HOME:-}/PrixOk"
        "${HOME:-}/prixok"
        "${HOME:-}/storage/downloads/PrixOk"
        "${HOME:-}/storage/downloads/prixok"
    )

    for candidate in "${candidates[@]}"; do
        [[ -n "${candidate}" ]] || continue
        if is_bot_root "${candidate}"; then
            BOT_ROOT="${candidate}"
            return 0
        fi
    done

    return 1
}

if [[ -n "${BOT_ROOT}" ]]; then
    BOT_ROOT="$(cd -- "${BOT_ROOT}" 2>/dev/null && pwd)" || {
        echo "Không mở được thư mục bot: ${BOT_ROOT}" >&2
        exit 2
    }
elif ! detect_bot_root; then
    echo "Không tìm thấy thư mục mã nguồn PrixOk trên máy này." >&2
    echo "Hãy chạy script trên máy/container đang chứa bot:" >&2
    echo "  ./apply_atri_provider_audit_fix.sh /duong/dan/toi/PrixOk" >&2
    echo "Ví dụ trong container Docker: ... /app" >&2
    exit 2
fi

if ! is_bot_root "${BOT_ROOT}"; then
    echo "${BOT_ROOT} không phải thư mục gốc của bot PrixOk." >&2
    echo "Thiếu: ${BOT_ROOT}/bot/modules/atri_free_pool.py" >&2
    exit 2
fi

TEMP_PARENT="${TMPDIR:-${BOT_ROOT}}"
if [[ ! -d "${TEMP_PARENT}" || ! -w "${TEMP_PARENT}" ]]; then
    TEMP_PARENT="${BOT_ROOT}"
fi
TEMP_DIR="$(mktemp -d "${TEMP_PARENT%/}/.atri-provider-fix-download.XXXXXX")"
BACKUP_DIR=""
ROLLBACK_REQUIRED=0

cleanup() {
    rm -rf -- "${TEMP_DIR}"
}

rollback() {
    local status=$?

    if (( ROLLBACK_REQUIRED == 1 )) && [[ -n "${BACKUP_DIR}" ]]; then
        echo "Triển khai lỗi; đang khôi phục từ ${BACKUP_DIR}." >&2
        for relative in "${FILES[@]}"; do
            if [[ -f "${BACKUP_DIR}/${relative}" ]]; then
                install -D -m 0644 \
                    "${BACKUP_DIR}/${relative}" \
                    "${BOT_ROOT}/${relative}"
            else
                rm -f -- "${BOT_ROOT}/${relative}"
            fi
        done
    fi

    cleanup
    exit "${status}"
}

trap rollback ERR INT TERM
trap cleanup EXIT

fetch_file() {
    local relative="$1"
    local destination="${TEMP_DIR}/${relative}"
    local url="${RAW_BASE}/${relative}"

    mkdir -p -- "$(dirname -- "${destination}")"

    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --silent --show-error \
            "${url}" --output "${destination}"
    elif command -v wget >/dev/null 2>&1; then
        wget -q "${url}" -O "${destination}"
    else
        echo "Cần curl hoặc wget để tải file sửa từ GitHub." >&2
        return 1
    fi
}

USE_LOCAL_SOURCE=1
for relative in "${FILES[@]}"; do
    if [[ ! -f "${LOCAL_SOURCE_ROOT}/${relative}" ]]; then
        USE_LOCAL_SOURCE=0
        break
    fi
done

if (( USE_LOCAL_SOURCE == 1 )); then
    SOURCE_ROOT="${LOCAL_SOURCE_ROOT}"
    echo "Dùng file sửa trong checkout hiện tại."
else
    SOURCE_ROOT="${TEMP_DIR}"
    echo "Đang tải file sửa từ ${REPOSITORY}@${SOURCE_REF:0:12}..."
    for relative in "${FILES[@]}"; do
        fetch_file "${relative}"
    done
fi

BACKUP_DIR="$(mktemp -d "${BOT_ROOT}/.atri-provider-audit-backup.XXXXXX")"
ROLLBACK_REQUIRED=1

for relative in "${FILES[@]}"; do
    source_file="${SOURCE_ROOT}/${relative}"
    target_file="${BOT_ROOT}/${relative}"

    if [[ ! -s "${source_file}" ]]; then
        echo "File nguồn rỗng hoặc bị thiếu: ${source_file}" >&2
        false
    fi

    if [[ -f "${target_file}" ]]; then
        install -D -m 0600 \
            "${target_file}" \
            "${BACKUP_DIR}/${relative}"
    fi

    if [[ "$(realpath -m "${source_file}")" != "$(realpath -m "${target_file}")" ]]; then
        install -D -m 0644 "${source_file}" "${target_file}"
    fi
done

PYTHON_BIN="${BOT_ROOT}/mltbenv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="$(command -v python3 || command -v python)"
fi

"${PYTHON_BIN}" -m compileall -q \
    "${BOT_ROOT}/bot/modules/atri_provider_config.py" \
    "${BOT_ROOT}/bot/modules/atri_provider_request.py" \
    "${BOT_ROOT}/bot/modules/atri_provider_capabilities.py" \
    "${BOT_ROOT}/bot/modules/atri_provider_control.py" \
    "${BOT_ROOT}/bot/modules/atri_free_pool.py"

if grep -Eq "NOVITA_API_KEY|novita_ling|novita_macaron" \
    "${BOT_ROOT}/bot/modules/atri_free_pool.py"; then
    echo "Novita vẫn còn trong free pool sau triển khai." >&2
    false
fi

ROLLBACK_REQUIRED=0
trap - ERR INT TERM

echo "Đã cập nhật provider runtime và audit tại ${BOT_ROOT}."
echo "Backup: ${BACKUP_DIR}"
echo "Hãy restart tiến trình/container bot để nạp mã mới."
