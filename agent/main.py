import sys
import os

from maa.agent.agent_server import AgentServer
from maa.tasker import Tasker

# 先导入自定义 Action 模块，让装饰器注册
import bbc_action
import sequential_tasks_action


def main():
    # 使用相对于 agent 文件的绝对路径
    agent_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(os.path.dirname(agent_dir), 'logs')
    Tasker.set_log_dir(log_dir)

    if len(sys.argv) < 2:
        print("Usage: python main.py <socket_id>")
        print("socket_id is provided by AgentIdentifier.")
        sys.exit(1)

    socket_id = sys.argv[-1]

    AgentServer.start_up(socket_id)
    AgentServer.join()
    AgentServer.shut_down()


if __name__ == "__main__":
    main()
