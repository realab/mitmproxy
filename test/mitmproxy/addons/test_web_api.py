import errno
import json
from unittest.mock import patch

import pytest
import tornado.testing
from tornado import httpclient
from tornado.web import create_signed_value

import mitmproxy.master
from mitmproxy import addons
from mitmproxy import log
from mitmproxy import options
from mitmproxy.addons import eventstore
from mitmproxy.addons import view
from mitmproxy.addons import web_api
from mitmproxy.addons.proxyserver import Proxyserver
from mitmproxy.test import taddons
from mitmproxy.test import tflow
from mitmproxy.tools.web import app
from mitmproxy.tools.web.webaddons import WebAuth


class TestWebAPIAddon:
    def test_load_registers_options(self):
        addon = web_api.WebAPIAddon()
        with taddons.context(addon) as tctx:
            assert tctx.options.web_port == 8081
            assert tctx.options.web_host == "127.0.0.1"

    def test_load_registers_web_auth(self):
        addon = web_api.WebAPIAddon()
        with taddons.context(addon) as tctx:
            auth = tctx.master.addons.get("webauth")
            assert auth is not None
            assert isinstance(auth, WebAuth)

    def test_done_without_running(self):
        addon = web_api.WebAPIAddon()
        with taddons.context(addon):
            assert addon._http_server is None
            addon.done()
            assert addon._http_server is None

    def test_module_addons_list(self):
        assert len(web_api.addons) == 1
        assert isinstance(web_api.addons[0], web_api.WebAPIAddon)

    async def test_running_and_done(self):
        addon = web_api.WebAPIAddon()
        with taddons.context(addon) as tctx:
            v = view.View()
            es = eventstore.EventStore()
            ps = Proxyserver()
            tctx.master.view = v
            tctx.master.events = es
            tctx.master.addons.add(ps, v, es)
            # Don't set master.proxyserver — let running() set it via hasattr check.

            tctx.options.update(web_port=0)

            addon.running()
            assert addon._http_server is not None
            assert tctx.master.proxyserver is ps

            addon.done()
            assert addon._http_server is None

    async def test_running_port_in_use(self):
        addon = web_api.WebAPIAddon()
        with taddons.context(addon) as tctx:
            v = view.View()
            es = eventstore.EventStore()
            ps = Proxyserver()
            tctx.master.view = v
            tctx.master.events = es
            tctx.master.addons.add(ps, v, es)
            tctx.master.proxyserver = ps

            tctx.options.update(web_port=0)

            oserror = OSError(errno.EADDRINUSE, "Address already in use")
            with patch.object(
                tornado.httpserver.HTTPServer, "listen", side_effect=oserror
            ):
                with pytest.raises(OSError, match="Try specifying a different port"):
                    addon.running()

    async def test_running_listen_oserror(self):
        addon = web_api.WebAPIAddon()
        with taddons.context(addon) as tctx:
            v = view.View()
            es = eventstore.EventStore()
            ps = Proxyserver()
            tctx.master.view = v
            tctx.master.events = es
            tctx.master.addons.add(ps, v, es)
            tctx.master.proxyserver = ps

            tctx.options.update(web_port=0)

            oserror = OSError(errno.EACCES, "Permission denied")
            with patch.object(
                tornado.httpserver.HTTPServer, "listen", side_effect=oserror
            ):
                with pytest.raises(OSError, match="Web API server failed to listen"):
                    addon.running()

    async def test_signal_handlers(self):
        addon = web_api.WebAPIAddon()
        with taddons.context(addon) as tctx:
            ps = Proxyserver()
            tctx.master.addons.add(ps)
            tctx.master.proxyserver = ps

            f = tflow.tflow(resp=True)
            addon._sig_view_add(f)
            addon._sig_view_update(f)
            addon._sig_view_remove(f, 0)
            addon._sig_view_refresh()
            addon._sig_events_add(log.LogEntry("test", "info"))
            addon._sig_events_refresh()
            addon._sig_options_update({"web_port"})
            addon._sig_servers_changed()


def get_json(resp: httpclient.HTTPResponse):
    return json.loads(resp.body.decode())


class TestWebAPIApp(tornado.testing.AsyncHTTPTestCase):
    """Integration test: verifies that the app created via the addon setup works."""

    def get_app(self):
        async def make_master():
            o = options.Options(http2=False)
            m = mitmproxy.master.Master(o)
            m.view = view.View()
            m.events = eventstore.EventStore()

            m.addons.add(*addons.default_addons())

            # Add the WebAPIAddon (registers web_port/web_host options and WebAuth).
            self._addon = web_api.WebAPIAddon()
            m.addons.add(self._addon)
            m.addons.add(m.view, m.events)

            m.proxyserver = m.addons.get("proxyserver")
            return m

        m = self.io_loop.asyncio_loop.run_until_complete(make_master())

        f = tflow.tflow(resp=True)
        f.id = "42"
        f.request.content = b"hello"
        m.view.add([f])

        m.events._add_log(log.LogEntry("test log", "info"))
        m.events.done()

        self.master = m
        self.view = m.view
        self.events = m.events

        webapp = app.Application(m, debug=False)
        webapp.settings["xsrf_cookies"] = False
        return webapp

    @property
    def auth_cookie(self) -> str:
        auth_cookie = create_signed_value(
            secret=self._app.settings["cookie_secret"],
            name=self._app.settings["auth_cookie_name"](),
            value=app.AuthRequestHandler.AUTH_COOKIE_VALUE,
        ).decode()
        return f"{self._app.settings['auth_cookie_name']()}={auth_cookie}"

    def fetch(self, *args, **kwargs) -> httpclient.HTTPResponse:
        kwargs.setdefault("headers", {}).setdefault("Cookie", self.auth_cookie)
        return super().fetch(*args, **kwargs, allow_nonstandard_methods=True)

    def test_flows(self):
        resp = self.fetch("/flows")
        assert resp.code == 200
        flows = get_json(resp)
        assert len(flows) == 1
        assert flows[0]["id"] == "42"

    def test_events(self):
        resp = self.fetch("/events")
        assert resp.code == 200
        events = get_json(resp)
        assert len(events) == 1
        assert events[0]["message"] == "test log"

    def test_state(self):
        resp = self.fetch("/state")
        assert resp.code == 200
        state = get_json(resp)
        assert "version" in state
        assert "servers" in state

    def test_options(self):
        resp = self.fetch("/options")
        assert resp.code == 200

    def test_flow_content(self):
        resp = self.fetch("/flows/42/request/content.data")
        assert resp.code == 200
        assert resp.body == b"hello"

    def test_flow_not_found(self):
        resp = self.fetch("/flows/nonexistent", method="DELETE")
        assert resp.code == 404
