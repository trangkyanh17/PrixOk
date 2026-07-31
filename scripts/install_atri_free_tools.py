from pathlib import Path
from shutil import copy2
from datetime import datetime

root = Path.cwd()
handlers = root / 'bot/core/handlers.py'
module = root / 'bot/modules/atri_free_tools.py'

if not handlers.is_file() or not module.is_file():
    raise SystemExit('Hãy chạy script trong /home/prix/PrixOk sau khi đã chép atri_free_tools.py.')

stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
copy2(handlers, Path('/root') / f'handlers.before-free-tools-{stamp}.py')
text = handlers.read_text(encoding='utf-8')

old_web_import = 'from ..modules.atri_tools import atri_tools_message, sync_bot_command_menu'
if old_web_import in text and (root / 'bot/modules/atri_tools').is_dir():
    old_file = root / 'bot/modules/atri_tools.py'
    new_file = root / 'bot/modules/atri_web_tools.py'
    if old_file.is_file() and not new_file.exists():
        old_file.rename(new_file)
    text = text.replace(
        old_web_import,
        'from ..modules.atri_web_tools import atri_tools_message, sync_bot_command_menu',
    )

import_line = 'from ..modules.atri_free_tools import atri_free_tools_message, start_free_tools\n'
if import_line not in text:
    anchors = (
        'from ..modules.atri_web_tools import atri_tools_message, sync_bot_command_menu\n',
        'from ..modules.atri_ai import atri_message\n',
    )
    for anchor in anchors:
        if anchor in text:
            text = text.replace(anchor, anchor + import_line, 1)
            break
    else:
        raise SystemExit('Không tìm thấy vị trí import Atri trong handlers.py.')

if 'MessageHandler(\n            atri_free_tools_message,' not in text:
    anchors = (
        '    TgClient.bot.add_handler(\n        MessageHandler(\n            atri_tools_message,',
        '    TgClient.bot.add_handler(\n        MessageHandler(\n            atri_message,',
    )
    for anchor in anchors:
        pos = text.find(anchor)
        if pos >= 0:
            block = '''    TgClient.bot.add_handler(
        MessageHandler(
            atri_free_tools_message,
            filters=(
                filters.incoming
                & filters.text
                & CustomFilters.authorized
            ),
        ),
        group=18,
    )
'''
            text = text[:pos] + block + text[pos:]
            break
    else:
        raise SystemExit('Không tìm thấy vị trí thêm handler Atri.')

start_marker = '_free_tools_loop.create_task(start_free_tools(TgClient.bot))'
if start_marker not in text:
    anchor = 'def add_handlers():\n'
    if anchor not in text:
        raise SystemExit('Không tìm thấy hàm add_handlers().')
    start = '''def add_handlers():
    from bot import bot_loop as _free_tools_loop

    _free_tools_loop.create_task(start_free_tools(TgClient.bot))
'''
    text = text.replace(anchor, start, 1)

handlers.write_text(text, encoding='utf-8')
print('Đã thêm lịch, nhắc việc, nhạc và Douyin vào handlers.py.')
print(f'Backup: /root/handlers.before-free-tools-{stamp}.py')
