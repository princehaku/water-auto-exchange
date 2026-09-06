"""Run isolated Lua 5.1 controller tests. Install lupa in the local venv."""
from pathlib import Path
from lupa.lua51 import LuaRuntime

root = Path(__file__).resolve().parents[1]
for path in sorted((root / 'tests').glob('*_test.lua')):
    runtime = LuaRuntime(unpack_returned_tuples=True)
    runtime.globals().arg = runtime.table_from({1: root.as_posix()})
    runtime.execute(path.read_text(encoding='utf-8-sig'))
