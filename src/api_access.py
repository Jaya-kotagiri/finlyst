"""Local development access boundary; this is not a public deployment/auth system."""

from ipaddress import ip_address
from urllib.parse import urlsplit


def local_request(request):
    """Reject non-loopback peers, rebinding hosts, proxies and cross-origin browsers."""
    try:
        if request.client is None or not ip_address(request.client.host).is_loopback:
            return False
        host = request.headers.get('host', '')
        parsed = urlsplit('http://' + host)
        if (parsed.hostname not in ('localhost', '127.0.0.1', '::1') or parsed.username is not None
                or parsed.path or parsed.query or parsed.fragment):
            return False
        port = parsed.port or (443 if request.url.scheme == 'https' else 80)
        if any(key == 'forwarded' or key == 'x-real-ip' or key.startswith('x-forwarded-') for key in request.headers):
            return False
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return False
        origin = request.headers.get('origin')
        if origin is not None:
            source = urlsplit(origin)
            if (source.scheme != request.url.scheme or source.hostname != parsed.hostname
                    or (source.port or (443 if source.scheme == 'https' else 80)) != port
                    or source.username is not None or source.path or source.query or source.fragment):
                return False
        return True
    except (ValueError, TypeError):
        return False
