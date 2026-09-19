#!/usr/bin/env python
"""Write a base64-encoded blob to a file — a workaround for `az container exec`
having no shell behind it (no redirection, no heredocs, no quoting; the
command string is split on whitespace with nothing respected). Passing a
destination path and a base64 blob as two plain argv tokens (base64 has no
internal whitespace) sidesteps that entirely.

Usage: python write_b64_file.py <dest_path> <base64_content>
"""
import base64
import sys

if __name__ == "__main__":
    dest, b64 = sys.argv[1], sys.argv[2]
    with open(dest, "wb") as f:
        f.write(base64.b64decode(b64))
    print(f"wrote {dest} ({len(base64.b64decode(b64))} bytes)")
