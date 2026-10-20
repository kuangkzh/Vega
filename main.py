"""入口：python main.py -c xxx.json"""
from __future__ import annotations

import argparse
import os

from core.config import load_config, build_model
from core.session_manager import SessionManager
from core.task_manager import TaskManager
from models.openai import OpenAIModel
from plugins.runtime import PluginRuntime
from storage.store import Store
from web.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Vega")
    parser.add_argument("-c", "--config")
    args = parser.parse_args()

    config_path = os.path.abspath(args.config)
    config = load_config(config_path)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    plugins_dir = os.path.join(base_dir, "plugins")
    data_dir = os.path.join(base_dir, "data")

    # 回退工作路径：Vega 目录本身（正常由 project.path 覆盖）
    default_cwd = base_dir

    print(f"[Vega] 配置文件: {config_path}")

    store = Store(os.path.join(data_dir, "vega.db"))

    def model_provider(model_id=None):
        mc = build_model(config, model_id)
        return OpenAIModel(mc) if mc else None

    task_manager = TaskManager(store)
    runtime = PluginRuntime(
        plugins_dir, config, default_cwd,
        task_manager=task_manager, im_link=None, model_provider=model_provider,
    )
    manager = SessionManager(store, runtime, config, default_cwd, model_provider).load()
    task_manager.on_done = manager.on_task_done

    app = create_app(config, store, manager, runtime, task_manager, default_cwd, config_path=config_path)
    web = config.get("web") or {}
    app.run(host=web.get("host", "127.0.0.1"), port=int(web.get("port", 8000)), threaded=True, debug=False)


if __name__ == "__main__":
    main()