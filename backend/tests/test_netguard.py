import os
import unittest
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "netguard-tests-only-not-a-real-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import images, netguard
from app.config import settings
from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models import User
from app.netguard import UnsafeURLError, check_outbound_url
from app.notifiers import discord, gotify, ntfy
from app.notifiers.errors import NotifierError
from app.routers import notifications

PUBLIC = "93.184.216.34"


def fake_dns(table):
    """name -> ip (or list of ips). Unlisted names resolve to a public address,
    and numeric hosts resolve to themselves, so no test touches real DNS."""

    def getaddrinfo(host, port, *args, **kwargs):
        value = table.get(host)
        if value is None:
            try:
                import ipaddress

                ipaddress.ip_address(host)
                value = host
            except ValueError:
                value = PUBLIC
        ips = value if isinstance(value, list) else [value]
        return [(2, 1, 6, "", (ip, port or 0)) for ip in ips]

    return patch("app.netguard.socket.getaddrinfo", getaddrinfo)


class NetguardBase(unittest.TestCase):
    def setUp(self):
        self.previous = settings.internal_ip_allow_list
        settings.internal_ip_allow_list = ""
        self.table = {}
        dns = fake_dns(self.table)
        dns.start()
        self.addCleanup(dns.stop)

    def tearDown(self):
        settings.internal_ip_allow_list = self.previous

    def allow(self, value):
        settings.internal_ip_allow_list = value

    def blocked(self, url):
        with self.assertRaises(UnsafeURLError) as error:
            check_outbound_url(url)
        return str(error.exception)


class EntryParsingTests(unittest.TestCase):
    def test_valid_entries(self):
        parse = netguard._parse_entry
        self.assertEqual(parse("10.0.0.5"), netguard.Rule(None, netguard.ipaddress.ip_network("10.0.0.5/32"), None))
        self.assertEqual(parse("10.0.0.5:3001").port, 3001)
        self.assertEqual(str(parse("192.168.1.0/24").network), "192.168.1.0/24")
        self.assertEqual((str(parse("192.168.1.0/24:8080").network), parse("192.168.1.0/24:8080").port), ("192.168.1.0/24", 8080))
        self.assertEqual((parse("Gotify:80").host, parse("Gotify:80").port), ("gotify", 80))
        self.assertEqual(parse("my-host.lan").host, "my-host.lan")
        self.assertEqual((str(parse("[::1]:3001").network), parse("[::1]:3001").port), ("::1/128", 3001))
        self.assertEqual(str(parse("[fd00::5]").network), "fd00::5/128")
        self.assertEqual(str(parse("fd00::/8").network), "fd00::/8")  # bare IPv6 network, no port
        self.assertEqual(str(parse("::1").network), "::1/128")

    def test_invalid_entries(self):
        for entry in [":80", "10.0.0.5:0", "10.0.0.5:99999", "10.0.0.5:abc", "[::1", "[::1]x", "ex ample.com", "http://10.0.0.5", "a/b"]:
            with self.assertRaises(ValueError, msg=entry):
                netguard._parse_entry(entry)

    def test_list_skips_bad_entries_with_a_warning_and_never_widens_access(self):
        with self.assertLogs("app.netguard", "WARNING") as logs:
            rules = netguard._parse_list("10.0.0.5:3001, ,bad host,10.0.0.9:abc, gotify")
        self.assertEqual(len(rules), 2)
        self.assertEqual(len(logs.records), 2)


class CheckOutboundUrlTests(NetguardBase):
    def test_public_addresses_are_allowed(self):
        check_outbound_url("https://example.com/")
        check_outbound_url(f"http://{PUBLIC}:8080/x")
        check_outbound_url("https://[2606:4700:4700::1111]/")

    def test_non_public_ranges_are_blocked_by_default(self):
        for host in ["127.0.0.1", "10.1.2.3", "172.16.5.5", "192.168.1.2", "100.64.0.1", "0.0.0.0", "192.0.2.1", "224.0.0.1", "[::1]", "[fd00::1]", "[::ffff:127.0.0.1]"]:
            message = self.blocked(f"http://{host}/")
            self.assertIn("INTERNAL_IP_ALLOW_LIST", message, host)

    def test_hostnames_resolving_to_private_addresses_are_blocked_without_leaking_the_address(self):
        self.table["gotify.lan"] = "10.0.0.5"
        message = self.blocked("http://gotify.lan:3001/")
        self.assertIn("gotify.lan:3001", message)
        self.assertNotIn("10.0.0.5", message)

    def test_ip_and_port_entries(self):
        self.allow("10.0.0.5:3001")
        check_outbound_url("http://10.0.0.5:3001/message")
        self.blocked("http://10.0.0.5:3002/")
        self.blocked("http://10.0.0.5/")  # default port 80 is not 3001
        self.blocked("http://10.0.0.6:3001/")

    def test_entries_without_a_port_allow_any_port(self):
        self.allow("10.0.0.5")
        for url in ["http://10.0.0.5/", "https://10.0.0.5/", "http://10.0.0.5:9999/"]:
            check_outbound_url(url)
        self.blocked("http://10.0.0.6/")

    def test_default_ports_are_matched(self):
        self.allow("10.0.0.5:80, 10.0.0.6:443")
        check_outbound_url("http://10.0.0.5/")
        check_outbound_url("https://10.0.0.6/")
        self.blocked("https://10.0.0.5/")

    def test_networks(self):
        self.allow("192.168.1.0/24")
        check_outbound_url("http://192.168.1.77:8080/")
        self.blocked("http://192.168.2.1/")
        self.allow("192.168.1.0/24:8080")
        check_outbound_url("http://192.168.1.77:8080/")
        self.blocked("http://192.168.1.77:81/")

    def test_ip_entries_match_the_address_a_hostname_resolves_to(self):
        self.table["nas.lan"] = "10.0.0.5"
        self.allow("10.0.0.5:3001")
        check_outbound_url("http://nas.lan:3001/")

    def test_hostname_entries_match_the_name_not_the_address(self):
        self.table.update({"gotify": "172.18.0.5", "other": "172.18.0.5"})
        self.allow("gotify:80")
        check_outbound_url("http://gotify/")
        check_outbound_url("http://GOTIFY./")
        self.blocked("http://gotify:81/")
        self.blocked("http://other/")  # same address, different name

    def test_every_resolved_address_must_pass(self):
        # A name answering with both a public and a private address is the
        # classic way to sneak past a check that only looks at one of them.
        # Both orders, and with a public address that sorts before the private one.
        self.table["a.example.com"] = ["1.1.1.1", "10.0.0.5"]
        self.table["b.example.com"] = ["10.0.0.5", "1.1.1.1"]
        self.table["c.example.com"] = [PUBLIC, "192.168.1.5"]
        for name in ["a", "b", "c"]:
            self.blocked(f"http://{name}.example.com/")
        self.allow("10.0.0.5")
        check_outbound_url("http://a.example.com/")
        check_outbound_url("http://b.example.com/")
        self.blocked("http://c.example.com/")  # its private address is a different one

    def test_ipv6_and_mapped_addresses(self):
        self.allow("[::1]:3001, 127.0.0.1:8000")
        check_outbound_url("http://[::1]:3001/")
        self.blocked("http://[::1]:3002/")
        check_outbound_url("http://[::ffff:127.0.0.1]:8000/")  # the mapped form of a listed IPv4 address
        self.blocked("http://[::ffff:127.0.0.1]:8001/")

    def test_link_local_is_never_allowed_even_when_listed(self):
        self.allow("169.254.169.254, 169.254.0.0/16, fe80::/10, metadata:80")
        self.table["metadata"] = "169.254.169.254"
        for url in ["http://169.254.169.254/latest/meta-data/", "http://[fe80::1]/", "http://metadata/", "http://[::ffff:169.254.169.254]/"]:
            self.assertIn("link-local", self.blocked(url), url)

    def test_malformed_and_unsupported_urls(self):
        for url in ["ftp://example.com/", "file:///etc/passwd", "not a url", "http://", "http:///x", "http://10.0.0.5:abc/", "javascript:alert(1)", ""]:
            self.blocked(url)

    def test_parser_differentials_cannot_smuggle_an_internal_host(self):
        # httpx connects to whatever host it parses, so that is the host checked.
        self.assertIn("127.0.0.1", self.blocked("http://example.com\\@127.0.0.1/x").replace("[", ""))
        check_outbound_url("http://127.0.0.1\\@example.com/x")  # httpx's host here is example.com
        self.assertIn("127.0.0.1", self.blocked("http://user:pass@127.0.0.1:8000/x"))

    def test_numeric_host_tricks_are_judged_by_what_they_resolve_to(self):
        with patch("app.netguard.socket.getaddrinfo", lambda host, port, *a, **k: [(2, 1, 6, "", ("127.0.0.1", port))]):
            self.blocked("http://2130706433/")  # decimal form of 127.0.0.1
            self.blocked("http://0x7f.1/")

    def test_unresolvable_names_are_rejected(self):
        with patch("app.netguard.socket.getaddrinfo", side_effect=OSError):
            self.assertIn("resolve", self.blocked("http://nope.invalid/"))


class IntegrationTests(NetguardBase):
    def setUp(self):
        super().setUp()
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add(User(id=1, username="Tester", email="one@example.com"))
            db.commit()
        app = FastAPI()
        app.include_router(notifications.router)

        def get_test_db():
            with self.sessions() as db:
                yield db

        def get_test_user():
            with self.sessions() as db:
                return db.get(User, 1)

        app.dependency_overrides[get_db] = get_test_db
        app.dependency_overrides[get_current_user] = get_test_user
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.addCleanup(self.engine.dispose)

    def save(self, channel, config):
        return self.client.put(f"/notifications/channels/{channel}", json={"enabled": True, "config": config})

    def test_saving_an_internal_server_is_refused_until_it_is_allowed(self):
        cases = [
            ("ntfy", {"topic": "t", "server": "http://10.0.0.5:8080"}, "ntfy server"),
            ("discord", {"webhook_url": "http://10.0.0.5:8080/hook"}, "Discord webhook"),
            ("gotify", {"server": "http://10.0.0.5:8080", "app_token": "t"}, "Gotify server"),
        ]
        for channel, config, label in cases:
            res = self.save(channel, config)
            self.assertEqual(res.status_code, 400, (channel, res.text))
            self.assertIn(label, res.json()["detail"])
            self.assertIn("INTERNAL_IP_ALLOW_LIST", res.json()["detail"])
        self.allow("10.0.0.5:8080")
        for channel, config, _ in cases:
            self.assertEqual(self.save(channel, config).status_code, 200, channel)

    def test_public_servers_and_default_ntfy_still_work(self):
        self.assertEqual(self.save("ntfy", {"topic": "t"}).status_code, 200)
        self.assertEqual(self.save("ntfy", {"topic": "t", "server": "https://ntfy.example.com"}).status_code, 200)
        self.assertEqual(self.save("discord", {"webhook_url": "https://discord.com/api/webhooks/1/x"}).status_code, 200)
        self.assertEqual(self.save("gotify", {"server": "https://gotify.example.com", "app_token": "t"}).status_code, 200)

    def test_disabled_channels_can_still_be_saved(self):
        res = self.client.put("/notifications/channels/ntfy", json={"enabled": False, "config": {"server": "http://10.0.0.5"}})
        self.assertEqual(res.status_code, 200)

    def test_sending_re_checks_so_a_stale_config_cannot_reach_the_network(self):
        # Saved while the address was allowed, sent after the allow list changed.
        configs = [
            (ntfy, {"topic": "t", "server": "http://10.0.0.5:8080"}),
            (discord, {"webhook_url": "http://10.0.0.5:8080/hook"}),
            (gotify, {"server": "http://10.0.0.5:8080", "app_token": "t"}),
        ]
        with patch("app.netguard.httpx.Client", side_effect=AssertionError("a request was made")) as client:
            for notifier, config in configs:
                with self.assertRaises(NotifierError) as error:
                    notifier.send("T", "B", config)
                self.assertIn("INTERNAL_IP_ALLOW_LIST", str(error.exception))
            client.assert_not_called()

    def test_photo_urls_use_the_same_guard(self):
        with self.assertRaises(images.ImageError) as error:
            images._validate_public_url("http://10.0.0.5/avatar.png")
        self.assertIn("INTERNAL_IP_ALLOW_LIST", str(error.exception))
        self.allow("10.0.0.5")
        images._validate_public_url("http://10.0.0.5/avatar.png")
        images._validate_public_url("https://example.com/avatar.png")


if __name__ == "__main__":
    unittest.main()
