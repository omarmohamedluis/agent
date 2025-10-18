from __future__ import annotations

from agent.runtime import AgentRuntime


def main() -> None:
    runtime = AgentRuntime()
    runtime.bootstrap()
    runtime.listen_and_reply()


if __name__ == "__main__":
    main()
