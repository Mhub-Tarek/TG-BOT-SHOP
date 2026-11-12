from __future__ import annotations

import logging

import uvicorn

from config import get_settings
from refund_app.app import create_app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("refund")


if __name__ == "__main__":
    settings = get_settings()
    log.info(
        "Refund calculator starting on http://%s:%s",
        settings.REFUND_WEB_HOST,
        settings.REFUND_WEB_PORT,
    )
    uvicorn.run(
        create_app(),
        host=settings.REFUND_WEB_HOST,
        port=settings.REFUND_WEB_PORT,
        log_level="info",
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=settings.WEB_FORWARDED_ALLOW_IPS,
    )
