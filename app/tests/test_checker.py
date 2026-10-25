import httpx

from patrol.checker import check_site, make_client
from tests.conftest import fake_http


def run(outcome, url="https://site.example"):
    client = make_client(fake_http({url: outcome}))
    return check_site(client, "s", url, timeout=5)


def test_200_is_up():
    r = run(200)
    assert r.up and r.http_status == 200 and r.error is None


def test_redirect_codes_count_as_up():
    assert run(301).up  # final 3xx without a Location is still a server answering


def test_500_is_down_with_reason():
    r = run(503)
    assert not r.up and r.http_status == 503 and r.error == "HTTP 503"


def test_timeout_is_down():
    r = run(httpx.ReadTimeout("slow"))
    assert not r.up and r.http_status is None and r.error == "timeout after 5s"


def test_unresolvable_host_is_down():
    r = run(None, url="https://nowhere.invalid")
    assert not r.up and "ConnectError" in r.error


def test_redirect_is_followed():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(302, headers={"Location": "https://site.example/home"})
        return httpx.Response(200)
    client = make_client(httpx.MockTransport(handler))
    r = check_site(client, "s", "https://site.example/", timeout=5)
    assert r.up and r.http_status == 200
