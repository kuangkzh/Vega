"""入口：python main.py -c xxx.json -d c:\\xxx"""
from __future__ import annotations

import argparse
import os

from core.config import load_config, seed_model_config
from core.session_manager import SessionManager
from core.task_manager import TaskManager
from message.feishu import FeishuSource
from models.base import ModelRegistry
from models.openai import OpenAIModel
from plugins.base import PluginRuntime
from storage.store import Store
from web.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="CodeAgent")
    parser.add_argument("-c", "--config", default="config/default.json")
    parser.add_argument("-d", "--dir")
    args = parser.parse_args()

    cwd = os.path.abspath(args.dir)
    os.makedirs(cwd, exist_ok=True)
    config_path = os.path.abspath(args.config)
    config = load_config(config_path)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    plugins_dir = os.path.join(base_dir, "plugins")
    data_dir = os.path.join(base_dir, "data")

    # 数据库统一放在 CodeAgent 自己的 data/ 目录下，不污染 -d 打开的项目
    store = Store(os.path.join(data_dir, "codeagent.db"))
    seed_model_config(store, config)
    registry = ModelRegistry(store)

    def model_provider(model_id=None):
        mc = registry.get(model_id) if model_id else registry.active()
        if mc is None:
            mc = (registry.list() or [None])[0]
        return OpenAIModel(mc) if mc else None

    task_manager = TaskManager(store)
    feishu = FeishuSource(config.get("feishu") or {})
    runtime = PluginRuntime(
        plugins_dir, config, cwd, store, registry,
        task_manager=task_manager, message_source=feishu, model_provider=model_provider,
    )
    manager = SessionManager(store, runtime, config, cwd, model_provider).load()
    task_manager.on_done = manager.on_task_done

    if feishu.enabled:
        primary = manager.list()[0] if manager.list() else manager.create()
        feishu.start(lambda text, _sender_id: primary.send(text))

    app = create_app(config, store, manager, runtime, task_manager, registry, cwd, feishu=feishu, config_path=config_path)
    web = config.get("web") or {}
    app.run(host=web.get("host", "127.0.0.1"), port=int(web.get("port", 8000)), threaded=True, debug=False)


if __name__ == "__main__":
    main()