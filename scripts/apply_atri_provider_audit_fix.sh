#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
BOT_ROOT="${1:-/app}"

if [[ ! -f "${SOURCE_ROOT}/bot/modules/atri_provider_config.py" ]]; then
    echo "Nguồn fix không đầy đủ tại ${SOURCE_ROOT}." >&2
    exit 2
fi

if [[ ! -f "${BOT_ROOT}/bot/modules/atri_free_pool.py" ]]; then
    echo "Không tìm thấy bot PrixOk tại ${BOT_ROOT}." >&2
    exit 2
fi

FILES=(
    bot/modules/atri_provider_config.py
    bot/modules/atri_provider_request.py
    bot/modules/atri_provider_capabilities.py
    bot/modules/atri_provider_control.py
    bot/modules/atri_free_pool.py
)

BACKUP_DIR="$(mktemp -d "${BOT_ROOT}/.atri-provider-audit-backup.XXXXXX")"
ROLLBACK_REQUIRED=1

rollback() {
    local status=$?

    if (( ROLLBACK_REQUIRED == 1 )); then
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

    exit "${status}"
}

trap rollback ERR INT TERM

for relative in "${FILES[@]}"; do
    source_file="${SOURCE_ROOT}/${relative}"
    target_file="${BOT_ROOT}/${relative}"

    if [[ ! -f "${source_file}" ]]; then
        echo "Thiếu file nguồn ${source_file}." >&2
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
    PYTHON_BIN="$(command -v python3)"
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
