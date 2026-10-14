"""入口：python main.py -c xxx.json -d c:\\xxx"""
from __future__ import annotations

import argparse
import os

from core.config import load_config, build_model
from core.session_manager import SessionManager
from core.task_manager import TaskManager
from im_link.base import build_im_link
from models.openai import OpenAIModel
from plugins.runtime import PluginRuntime
from storage.store import Store
from web.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Vega")
    parser.add_argument("-c", "--config")
    parser.add_argument("-d", "--dir")
    args = parser.parse_args()

    cwd = os.path.abspath(args.dir)
    os.makedirs(cwd, exist_ok=True)
    config_path = os.path.abspath(args.config)
    config = load_config(config_path)

    print(f"[Vega] 工作目录(cwd): {cwd}")
    print(f"[Vega] 配置文件: {config_path}")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    plugins_dir = os.path.join(base_dir, "plugins")
    data_dir = os.path.join(base_dir, "data")

    # 数据库放在 Vega 自己的 data/ 目录下（仅存会话/任务），模型配置在 config JSON 里
    store = Store(os.path.join(data_dir, "vega.db"))

    def model_provider(model_id=None):
        mc = build_model(config, model_id)
        return OpenAIModel(mc) if mc else None

    task_manager = TaskManager(store)
    im_link = build_im_link(config.get("im_link") or {})
    runtime = PluginRuntime(
        plugins_dir, config, cwd,
        task_manager=task_manager, im_link=im_link, model_provider=model_provider,
    )
    manager = SessionManager(store, runtime, config, cwd, model_provider).load()
    task_manager.on_done = manager.on_task_done

    if im_link is not None:
        im_link.start(lambda text, _sender_id: manager.im_recv(text))

    app = create_app(config, store, manager, runtime, task_manager, cwd, config_path=config_path)
    web = config.get("web") or {}
    app.run(host=web.get("host", "127.0.0.1"), port=int(web.get("port", 8000)), threaded=True, debug=False)


if __name__ == "__main__":
    main()