import os, tempfile, requests
from urllib.parse import urlparse

def safe_filename_from_url(url: str) -> str:
    base = os.path.basename(urlparse(url).path) or "asset"
    return base.split("?")[0]

def download_to(path: str, url: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return path

def fetch_payload(payload_url: str) -> dict:
    r = requests.get(payload_url, timeout=60)
    r.raise_for_status()
    return r.json()

def tmpdir(prefix="job_") -> str:
    return tempfile.mkdtemp(prefix=prefix)
