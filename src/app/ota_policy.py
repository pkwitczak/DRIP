REQUIRED = ("env_reader.py", "net.py", "ota.py", "ota_policy.py", "post.py")
MAX_FILES = 128
MAX_FILE_BYTES = 256 * 1024
MAX_RELEASE_BYTES = 1024 * 1024
FORBIDDEN = ("core_settings.env", "main.py", "recovery.py")

def destination(path):
    if not isinstance(path, str) or not path or len(path) > 160:
        raise ValueError("Invalid destination")
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./-" for c in path):
        raise ValueError("Invalid path character")
    parts = path.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") or p in FORBIDDEN for p in parts):
        raise ValueError("Forbidden path")
    return path

def validate(manifest):
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("Unsupported manifest")
    version = manifest.get("version")
    if not isinstance(version, str) or not version or len(version) > 80:
        raise ValueError("Invalid version")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FILES:
        raise ValueError("Invalid files list")
    names, total = set(), 0
    for entry in entries:
        path = destination(entry["path"])
        if path in names:
            raise ValueError("Duplicate path")
        names.add(path)
        size, digest = entry["size"], entry["sha256"]
        if type(size) is not int or size < 0 or size > MAX_FILE_BYTES:
            raise ValueError("Invalid size")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid sha256")
        total += size
    if total > MAX_RELEASE_BYTES or any(p not in names for p in REQUIRED):
        raise ValueError("Incomplete or oversized release")
    for path in names:
        parts = path.split("/")
        if any("/".join(parts[:i]) in names for i in range(1, len(parts))):
            raise ValueError("File/directory collision")
    return entries
