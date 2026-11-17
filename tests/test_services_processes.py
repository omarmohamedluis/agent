import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from omi.services.processes import process_options, terminate_process


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux process-group/EOF integration")
async def test_guard_parent_eof_stops_child_and_grandchild(tmp_path):
    script = tmp_path / "tree.py"
    pids = tmp_path / "pids.json"
    script.write_text("import json,os,subprocess,sys,time\n"
                      "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                      "open(sys.argv[1],'w').write(json.dumps([os.getpid(),child.pid]))\n"
                      "time.sleep(60)\n")
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "omi.services.guard",
                    sys.executable, str(script), str(pids), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **process_options())
    try:
        for _ in range(100):
            if pids.exists():
                break
            await asyncio.sleep(0.02)
        children = json.loads(pids.read_text())
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 6)
        for pid in children:
            status = Path(f"/proc/{pid}/stat")
            # A zombie awaits PID1 reaping but cannot run or hold hardware.
            assert not status.exists() or status.read_text().split(")", 1)[1].strip().startswith("Z")
    finally:
        await terminate_process(process)
