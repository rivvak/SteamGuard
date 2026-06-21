"""
Run once after deploying to get your server's certificate SHA-256 fingerprint.
Paste the output into auth/client.py as _SERVER_CERT_HASH.

Usage:  python auth/get_cert_hash.py https://your-service.a.run.app
"""
import sys, ssl, socket, hashlib, urllib.parse

def get_fingerprint(url: str) -> str:
    parsed   = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    port     = parsed.port or 443
    ctx = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
            der = ssock.getpeercert(binary_form=True)
            return hashlib.sha256(der).hexdigest()

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else input("Enter server URL: ").strip()
    fp  = get_fingerprint(url)
    print(f"\nCertificate SHA-256 fingerprint:\n{fp}")
    print(f'\nPaste into auth/client.py:\n_SERVER_CERT_HASH = "{fp}"')
