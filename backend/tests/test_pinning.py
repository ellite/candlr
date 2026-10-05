import datetime
import http.server
import ipaddress
import os
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "pinning-tests-only-not-a-real-secret-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import images, netguard
from app.config import settings
from app.netguard import UnsafeURLError

PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


class Resolver:
    """A fake getaddrinfo: name -> list of answers per call. The last answer
    repeats, so [first, second] models a DNS record that changes."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, host, port, *args, **kwargs):
        self.calls.append(host)
        try:
            ipaddress.ip_address(host)
            sequence = [[host]]  # an address literal resolves to itself, as with the real resolver
        except ValueError:
            sequence = self.answers.get(host, [[PUBLIC]])
        number = sum(1 for call in self.calls if call == host) - 1
        ips = sequence[min(number, len(sequence) - 1)]
        return [(10 if ":" in ip else 2, 1, 6, "", (ip, port or 0)) for ip in ips]


class Transport:
    """Records what reached the network layer, as it was at that moment."""

    def __init__(self, respond=None):
        self.sent = []
        self.respond = respond or (lambda request: httpx.Response(200, text="ok"))

    def __call__(self, request):
        self.sent.append(
            {
                "url": str(request.url),
                "host": request.headers.get("host"),
                "sni": request.extensions.get("sni_hostname"),
                "marker": request.extensions.get("x_marker"),
                "headers": dict(request.headers),
                "body": request.content,
            }
        )
        return self.respond(request)


class PinningBase(unittest.TestCase):
    def setUp(self):
        self.previous = settings.internal_ip_allow_list
        settings.internal_ip_allow_list = ""
        self.resolver = Resolver()
        self.transport = Transport()
        self.clients = []
        real_client = httpx.Client

        def client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(self.transport)
            made = real_client(**kwargs)
            self.clients.append(made)
            return made

        for target in [
            patch("app.netguard.socket.getaddrinfo", self.resolver),
            patch("app.netguard.httpx.Client", client),
        ]:
            target.start()
            self.addCleanup(target.stop)

    def tearDown(self):
        settings.internal_ip_allow_list = self.previous


class RequestShapeTests(PinningBase):
    def test_connects_to_the_checked_address_and_keeps_the_hostname_for_host_and_tls(self):
        netguard.request("GET", "https://name.example.com:8443/a/b?x=1")
        sent = self.transport.sent[0]
        self.assertEqual(sent["url"], f"https://{PUBLIC}:8443/a/b?x=1")
        self.assertEqual(sent["host"], "name.example.com:8443")
        self.assertEqual(sent["sni"], "name.example.com")

    def test_default_ports_and_plain_http(self):
        netguard.request("GET", "http://name.example.com/x")
        sent = self.transport.sent[0]
        self.assertEqual((sent["url"], sent["host"], sent["sni"]), (f"http://{PUBLIC}/x", "name.example.com", None))

    def test_ipv6_answers_are_bracketed(self):
        self.resolver.answers["v6.example.com"] = [[PUBLIC_V6]]
        netguard.request("GET", "https://v6.example.com/")
        self.assertEqual(self.transport.sent[0]["url"], f"https://[{PUBLIC_V6}]/")
        self.assertEqual(self.transport.sent[0]["host"], "v6.example.com")

    def test_an_ip_literal_is_left_alone(self):
        settings.internal_ip_allow_list = "10.0.0.5"
        netguard.request("GET", "https://10.0.0.5:3001/x")
        sent = self.transport.sent[0]
        self.assertEqual((sent["url"], sent["host"], sent["sni"]), ("https://10.0.0.5:3001/x", "10.0.0.5:3001", None))

    def test_headers_auth_and_body_are_forwarded(self):
        netguard.request(
            "REPORT", "https://name.example.com/dav/", headers={"Depth": "1", "X-Test": "y"},
            auth=httpx.BasicAuth("u", "p"), content=b"<xml/>",
        )
        sent = self.transport.sent[0]
        self.assertEqual((sent["headers"]["depth"], sent["headers"]["x-test"]), ("1", "y"))
        self.assertTrue(sent["headers"]["authorization"].startswith("Basic "))
        self.assertEqual(sent["body"], b"<xml/>")
        netguard.request("POST", "https://name.example.com/m", json={"a": 1})
        self.assertEqual(self.transport.sent[1]["body"], b'{"a": 1}')

    def test_errors_name_the_requested_url_not_the_pinned_address(self):
        self.transport.respond = lambda request: httpx.Response(404)
        response = netguard.request("GET", "https://gotify.example.com/message")
        with self.assertRaises(httpx.HTTPStatusError) as error:
            response.raise_for_status()
        self.assertIn("gotify.example.com", str(error.exception))
        self.assertNotIn(PUBLIC, str(error.exception))

    def test_redirects_are_returned_not_followed(self):
        self.transport.respond = lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})
        response = netguard.request("GET", "https://name.example.com/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(self.transport.sent), 1)

    def test_streaming_leaves_the_body_unread_until_asked(self):
        produced = []

        def body():
            for _ in range(4):
                produced.append(1)
                yield b"x" * 25

        self.transport.respond = lambda request: httpx.Response(200, content=body())
        with netguard.stream("GET", "https://name.example.com/") as response:
            self.assertEqual(produced, [])  # nothing was read just by connecting
            self.assertEqual(sum(len(chunk) for chunk in response.iter_bytes()), 100)
        self.assertEqual(len(produced), 4)


class RebindingTests(PinningBase):
    def test_a_dns_answer_that_changes_after_the_check_cannot_redirect_the_request(self):
        # First lookup: a public address. Any later lookup: loopback.
        self.resolver.answers["rebind.example.com"] = [[PUBLIC], ["127.0.0.1"]]
        netguard.request("GET", "http://rebind.example.com/secret")
        self.assertEqual(self.resolver.calls.count("rebind.example.com"), 1)  # resolved exactly once
        self.assertEqual(self.transport.sent[0]["url"], f"http://{PUBLIC}/secret")

    def test_every_address_is_checked_before_any_connection(self):
        self.resolver.answers["mixed.example.com"] = [[PUBLIC, "10.0.0.5"]]
        with self.assertRaises(UnsafeURLError):
            netguard.request("GET", "http://mixed.example.com/")
        self.assertEqual(self.clients, [])  # no client was even created

    def test_blocked_urls_never_reach_the_network(self):
        for url in ["http://127.0.0.1/", "http://169.254.169.254/", "ftp://x.example.com/", "http://[::1]/"]:
            with self.assertRaises(UnsafeURLError):
                netguard.request("GET", url)
        self.assertEqual(self.transport.sent, [])
        self.assertEqual(self.clients, [])


class ClientAndFallbackTests(PinningBase):
    def test_a_fresh_client_per_request_which_is_closed_afterwards(self):
        # httpx pools connections by address, so a shared client would let a
        # connection verified for one hostname serve another.
        netguard.request("GET", "https://a.example.com/")
        netguard.request("GET", "https://b.example.com/")
        self.assertEqual(len(self.clients), 2)
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_the_next_address_is_tried_when_a_connection_fails(self):
        self.resolver.answers["multi.example.com"] = [["198.51.100.1", PUBLIC]]
        settings.internal_ip_allow_list = "198.51.100.1"  # documentation range, not public

        def respond(request):
            if request.url.host == "198.51.100.1":
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, text="from the second address")

        self.transport.respond = respond
        response = netguard.request("GET", "http://multi.example.com/")
        self.assertEqual(response.text, "from the second address")
        self.assertEqual([s["url"] for s in self.transport.sent], ["http://198.51.100.1/", f"http://{PUBLIC}/"])
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_extensions_survive_a_retry(self):
        self.resolver.answers["multi.example.com"] = [["198.51.100.1", PUBLIC]]
        settings.internal_ip_allow_list = "198.51.100.1"

        def respond(request):
            if request.url.host == "198.51.100.1":
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200)

        self.transport.respond = respond
        netguard.request("GET", "https://multi.example.com/", extensions={"x_marker": "kept"})
        self.assertEqual([s["sni"] for s in self.transport.sent], ["multi.example.com", "multi.example.com"])
        self.assertEqual([s["marker"] for s in self.transport.sent], ["kept", "kept"])  # the caller's extension too

    def test_all_addresses_failing_raises_the_connection_error(self):
        self.resolver.answers["down.example.com"] = [[PUBLIC, "93.184.216.35"]]
        self.transport.respond = lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request))
        with self.assertRaises(httpx.ConnectError):
            netguard.request("GET", "http://down.example.com/")
        self.assertEqual(len(self.transport.sent), 2)
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_other_errors_are_not_retried_on_another_address(self):
        self.resolver.answers["slow.example.com"] = [[PUBLIC, "93.184.216.35"]]
        self.transport.respond = lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=request))
        with self.assertRaises(httpx.ReadTimeout):
            netguard.request("GET", "http://slow.example.com/")
        self.assertEqual(len(self.transport.sent), 1)


class ImageDownloadTests(PinningBase):
    def test_every_redirect_hop_is_checked_and_pinned(self):
        hops = {
            "a.example.com": httpx.Response(302, headers={"location": "http://b.example.com/pic.png"}),
            "b.example.com": httpx.Response(200, content=b"imagebytes"),
        }
        self.resolver.answers.update({"a.example.com": [[PUBLIC]], "b.example.com": [["93.184.216.40"]]})
        self.transport.respond = lambda request: hops[request.headers["host"]]
        self.assertEqual(images.download_image("http://a.example.com/start"), b"imagebytes")
        self.assertEqual([s["url"] for s in self.transport.sent], [f"http://{PUBLIC}/start", "http://93.184.216.40/pic.png"])

    def test_a_redirect_to_an_internal_address_is_refused_before_connecting(self):
        self.transport.respond = lambda request: httpx.Response(302, headers={"location": "http://10.0.0.5/admin"})
        with self.assertRaises(images.ImageError) as error:
            images.download_image("http://a.example.com/start")
        self.assertIn("INTERNAL_IP_ALLOW_LIST", str(error.exception))
        self.assertEqual(len(self.transport.sent), 1)

    def test_a_redirect_loop_stops(self):
        self.transport.respond = lambda request: httpx.Response(302, headers={"location": "http://a.example.com/again"})
        with self.assertRaises(images.ImageError) as error:
            images.download_image("http://a.example.com/")
        self.assertIn("Too many redirects", str(error.exception))

    def test_the_size_cap_is_enforced_while_streaming(self):
        self.transport.respond = lambda request: httpx.Response(200, content=b"x" * 50)
        with patch.object(images, "MAX_IMAGE_BYTES", 10):
            with self.assertRaises(images.ImageError) as error:
                images.download_image("http://a.example.com/big.png")
        self.assertIn("too large", str(error.exception))

    def test_http_errors_become_image_errors(self):
        self.transport.respond = lambda request: httpx.Response(404)
        with self.assertRaises(images.ImageError):
            images.download_image("http://a.example.com/missing.png")


class RealTlsTests(unittest.TestCase):
    """Real sockets and a real TLS handshake against a local CA, to prove the
    certificate is checked against the hostname while the connection goes to
    the pinned address."""

    @classmethod
    def setUpClass(cls):
        def key():
            return ec.generate_private_key(ec.SECP256R1())

        def name(common_name):
            return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])

        now = datetime.datetime.now(datetime.timezone.utc)
        window = dict(not_valid_before=now - datetime.timedelta(days=1), not_valid_after=now + datetime.timedelta(days=1))
        ca_key = key()
        ca = (
            x509.CertificateBuilder().subject_name(name("Test CA")).issuer_name(name("Test CA"))
            .public_key(ca_key.public_key()).serial_number(1)
            .not_valid_before(window["not_valid_before"]).not_valid_after(window["not_valid_after"])
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        server_key = key()
        server = (
            x509.CertificateBuilder().subject_name(name("good.test")).issuer_name(ca.subject)
            .public_key(server_key.public_key()).serial_number(2)
            .not_valid_before(window["not_valid_before"]).not_valid_after(window["not_valid_after"])
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("good.test")]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        cls.directory = tempfile.TemporaryDirectory()
        base = cls.directory.name
        pem = serialization.Encoding.PEM
        cls.ca_path = f"{base}/ca.pem"
        Path(cls.ca_path).write_bytes(ca.public_bytes(pem))
        Path(f"{base}/server.pem").write_bytes(server.public_bytes(pem))
        Path(f"{base}/server.key").write_bytes(
            server_key.private_bytes(pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
        )

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = f"host={self.headers['Host']}".encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(f"{base}/server.pem", f"{base}/server.key")
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.directory.cleanup()

    def setUp(self):
        self.previous = settings.internal_ip_allow_list
        settings.internal_ip_allow_list = f"127.0.0.1:{self.port}"
        resolver = Resolver({"good.test": [["127.0.0.1"]], "evil.test": [["127.0.0.1"]]})
        for target in [
            patch("app.netguard.socket.getaddrinfo", resolver),
            patch.dict(os.environ, {"SSL_CERT_FILE": self.ca_path}),
        ]:
            target.start()
            self.addCleanup(target.stop)

    def tearDown(self):
        settings.internal_ip_allow_list = self.previous

    def test_the_certificate_is_verified_against_the_hostname(self):
        response = netguard.request("GET", f"https://good.test:{self.port}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, f"host=good.test:{self.port}")

    def test_a_host_whose_certificate_is_for_another_name_is_rejected(self):
        # evil.test resolves to the same server, which only has a certificate for good.test.
        with self.assertRaises(httpx.ConnectError) as error:
            netguard.request("GET", f"https://evil.test:{self.port}/")
        self.assertIn("CERTIFICATE_VERIFY_FAILED", str(error.exception))

    def test_without_the_pin_the_ip_would_fail_verification(self):
        # Sanity check of the setup: connecting to the bare address (what pinning
        # without sni_hostname would do) cannot succeed against this certificate.
        with httpx.Client(timeout=5) as client:
            with self.assertRaises(httpx.ConnectError):
                client.get(f"https://127.0.0.1:{self.port}/")


if __name__ == "__main__":
    unittest.main()
