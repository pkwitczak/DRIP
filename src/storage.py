"""Shared filesystem operations."""
import os

def exists(path):
    try:
        os.stat(path)
        return True
    except OSError as exc:
        if exc.args and exc.args[0] == 2:
            return False
        raise


def sync():
    operation = getattr(os, "sync", None)

    if operation is not None:
        operation()


def mkdirs(path):
    current = "/" if path.startswith("/") else ""

    for part in path.split("/"):
        if not part:
            continue

        if current and not current.endswith("/"):
            current += "/"

        current += part

        if not exists(current):
            os.mkdir(current)


def read_text(path, limit=None):
    with open(path, "r") as handle:
        if limit is None:
            return handle.read()

        return handle.read(limit)


def _write_text(path, text, mode):
    with open(path, mode) as handle:
        written = handle.write(text)

        if written != len(text):
            raise OSError(
                "Incomplete file write: " + path
            )

        handle.flush()


def write_text(path, text):
    _write_text(path, text, "w")


def append_text(path, text):
    _write_text(path, text, "a")


def remove_tree(path, heartbeat=lambda: None):
    if not exists(path):
        return

    heartbeat()

    if os.stat(path)[0] & 0x4000:
        for name in os.listdir(path):
            remove_tree(
                path + "/" + name,
                heartbeat,
            )

        os.rmdir(path)
    else:
        os.remove(path)
