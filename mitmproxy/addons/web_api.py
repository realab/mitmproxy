"""
Addon that serves the mitmweb REST/WebSocket API alongside any mitmproxy frontend.

Usage:
    mitmproxy -s mitmproxy/addons/web_api.py --set web_port=8081
    mitmproxy -s mitmproxy/addons/web_api.py --set web_port=8081 --set web_password=secret
"""

from __future__ import annotations

import errno
import logging
from typing import Any

import tornado.httpserver
import tornado.ioloop

from mitmproxy import ctx
from mitmproxy import flow
from mitmproxy import log
from mitmproxy import optmanager
from mitmproxy.tools.web import app
from mitmproxy.tools.web.webaddons import WebAuth

logger = logging.getLogger(__name__)


class WebAPIAddon:
    """Serves the mitmweb REST/WebSocket API alongside any mitmproxy frontend."""

    def __init__(self):
        self._web_auth = WebAuth()
        self._http_server: tornado.httpserver.HTTPServer | None = None

    def load(self, loader):
        loader.add_option("web_port", int, 8081, "Web API port.")
        loader.add_option("web_host", str, "127.0.0.1", "Web API host.")
        ctx.master.addons.add(self._web_auth)

    def running(self):
        # master is typed as Any because this addon requires attributes (view, events,
        # proxyserver) that are on ConsoleMaster/WebMaster but not the base Master.
        master: Any = ctx.master

        # Ensure master.proxyserver is set (needed by State handler).
        if not hasattr(master, "proxyserver"):
            master.proxyserver = master.addons.get("proxyserver")

        # Create the tornado application (reuses all mitmweb handlers).
        self.app = app.Application(master, debug=False)

        # Connect signals for real-time WebSocket broadcasts.
        master.view.sig_view_add.connect(self._sig_view_add)
        master.view.sig_view_remove.connect(self._sig_view_remove)
        master.view.sig_view_update.connect(self._sig_view_update)
        master.view.sig_view_refresh.connect(self._sig_view_refresh)

        master.events.sig_add.connect(self._sig_events_add)
        master.events.sig_refresh.connect(self._sig_events_refresh)

        master.options.changed.connect(self._sig_options_update)
        master.proxyserver.servers.changed.connect(self._sig_servers_changed)

        # Start tornado HTTP server.
        tornado.ioloop.IOLoop.current()
        self._http_server = tornado.httpserver.HTTPServer(
            self.app, max_buffer_size=2**32
        )
        try:
            self._http_server.listen(ctx.options.web_port, ctx.options.web_host)
        except OSError as e:
            message = (
                f"Web API server failed to listen on "
                f"{ctx.options.web_host or '*'}:{ctx.options.web_port} with {e}"
            )
            if e.errno == errno.EADDRINUSE:
                message += (
                    f"\nTry specifying a different port by using "
                    f"`--set web_port={ctx.options.web_port + 2}`."
                )
            raise OSError(e.errno, message, e.filename) from e

        logger.info(
            f"Web API server listening at http://{ctx.options.web_host}:{ctx.options.web_port}/"
        )

    def done(self):
        if self._http_server:
            self._http_server.stop()
            self._http_server = None

    # Signal handlers (same pattern as WebMaster).

    def _sig_view_add(self, flow: flow.Flow) -> None:
        app.ClientConnection.broadcast_flow("flows/add", flow)

    def _sig_view_update(self, flow: flow.Flow) -> None:
        app.ClientConnection.broadcast_flow("flows/update", flow)

    def _sig_view_remove(self, flow: flow.Flow, index: int) -> None:
        app.ClientConnection.broadcast(type="flows/remove", payload=flow.id)

    def _sig_view_refresh(self) -> None:
        app.ClientConnection.broadcast_flow_reset()

    def _sig_events_add(self, entry: log.LogEntry) -> None:
        app.ClientConnection.broadcast(
            type="events/add",
            payload=app.logentry_to_json(entry),
        )

    def _sig_events_refresh(self) -> None:
        app.ClientConnection.broadcast(type="events/reset")

    def _sig_options_update(self, updated: set[str]) -> None:
        options_dict = optmanager.dump_dicts(ctx.master.options, updated)
        app.ClientConnection.broadcast(type="options/update", payload=options_dict)

    def _sig_servers_changed(self) -> None:
        master: Any = ctx.master
        app.ClientConnection.broadcast(
            type="state/update",
            payload={
                "servers": {
                    s.mode.full_spec: s.to_json()
                    for s in master.proxyserver.servers
                }
            },
        )


addons = [WebAPIAddon()]
