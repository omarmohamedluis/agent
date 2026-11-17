"""Bounded cleanup of isolated service process groups, including descendants."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess


def process_options() -> dict:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


async def terminate_process(process: asyncio.subprocess.Process, grace: float = 1.5):
    """Kill the whole original group even if its leader has already exited."""
    if process.stdin is not None:
        process.stdin.close()
    if os.name == "nt":
        # Windows has no killpg; taskkill /T tracks descendants while leader lives.
        # Give lifetime-pipe guardians time to start their child and observe EOF;
        # taskkill racing an early spawn can miss that just-created descendant.
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), grace)
            except asyncio.TimeoutError:
                pass
        if process.returncode is None:
            helper = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                                                          stdout=asyncio.subprocess.DEVNULL,
                                                          stderr=asyncio.subprocess.DEVNULL,
                                                          creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                await asyncio.wait_for(helper.wait(), grace)
            except asyncio.TimeoutError:
                helper.kill()
                await helper.wait()
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), grace)
        except asyncio.TimeoutError:
            pass
        # Leader exit alone is not evidence that Stream Deck helper children died.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(process.wait(), grace)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Service process {process.pid} did not terminate") from exc
    if process.stdin is not None:
        try:
            await asyncio.wait_for(process.stdin.wait_closed(), 0.5)
        except (BrokenPipeError, ConnectionError, asyncio.TimeoutError):
            pass
