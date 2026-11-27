from __future__ import annotations

import logging
import sys

from os_ken.lib import hub

if hasattr(hub, "patch"):
    hub.patch(thread=False)

from os_ken import cfg
from os_ken.base.app_manager import AppManager


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    apps = sys.argv[1:] if len(sys.argv) > 1 else ["src.ctrl.update_app"]
    if "os_ken.controller.ofp_handler" not in apps:
        apps = ["os_ken.controller.ofp_handler", *apps]
    app_mgr = AppManager.get_instance()
    app_mgr.load_apps(apps)
    cfg.CONF(args=[])
    contexts = app_mgr.create_contexts()
    services = app_mgr.instantiate_apps(**contexts)
    try:
        hub.joinall(services)
    finally:
        app_mgr.close()


if __name__ == "__main__":
    main()