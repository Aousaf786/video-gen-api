import os, tempfile, requests
from urllib.parse import urlparse

ASSETS_ROOT = "/workspace/assets"           # e.g., /workspace/assets  (Pods) or /tmp/assets (Serverless)
ASSET_URL_PREFIX = os.getenv("ASSET_URL_PREFIX") # e.g., https://my-cdn.example.com/assets/
DEFAULT_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))

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

def _asset_local_path(name_or_path: str) -> str:
    """Map 'asset://foo.jpg' or 'foo.jpg' to a local path under ASSETS_ROOT."""
    if not ASSETS_ROOT:
        return name_or_path
    name = name_or_path
    if name.startswith("asset://"):
        name = name[len("asset://"):]
    # prevent path traversal
    name = name.replace("..", "_")
    return os.path.join(ASSETS_ROOT, name)

def resolve_asset_src(src: str) -> str:
    """
    Accepts:
      - asset://filename.ext   -> try ASSETS_ROOT/filename.ext
      - /absolute/or/relative  -> use directly if exists
      - http(s)://...          -> return URL (to be downloaded by renderer)
    """
    if not src:
        return src
    # 1) asset:// schema -> local path under ASSETS_ROOT
    if src.startswith("asset://"):
        local = _asset_local_path(src)
        if os.path.exists(local):
            return local
        # optional: fallback to CDN prefix if present
        if ASSET_URL_PREFIX:
            return ASSET_URL_PREFIX.rstrip("/") + "/" + src[len("asset://"):]
        return src  # let renderer download if it's a URL
    # 2) local file path
    if os.path.exists(src):
        return src
    # 3) optional remap plain filenames when ASSETS_ROOT is set
    if ASSETS_ROOT and "://" not in src and not src.startswith("/"):
        candidate = os.path.join(ASSETS_ROOT, src)
        if os.path.exists(candidate):
            return candidate
    return src  # likely http(s) or something the renderer can handle


def fetch_payload(payload_url: str) -> dict:
    r = requests.get(payload_url, timeout=60)
    r.raise_for_status()
    return r.json()

def tmpdir(prefix="job_") -> str:
    return tempfile.mkdtemp(prefix=prefix)
