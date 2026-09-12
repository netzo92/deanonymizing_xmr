#!/usr/bin/env python3
"""Verify the pinned Monero release before extraction; isolated GPG keyring."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


VERSION = "0.18.5.1"
SOURCE_COMMIT = "4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5"
FINGERPRINT = "81AC591FE9C4B65C5806AFC3F0AF4D462A0BDF92"
ARCHIVE = f"monero-linux-x64-v{VERSION}.tar.bz2"
ARCHIVE_SHA256 = "22a7dda7b0cb699fdd6b7674c3b4a4465b337cc98a54983523b759e1e7cc9958"
ARCHIVE_URL = f"https://downloads.getmonero.org/cli/{ARCHIVE}"
HERE = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_signed_checksum(text):
    matches = [line.split() for line in text.splitlines()
               if len(line.split()) == 2 and line.split()[1] == ARCHIVE]
    if matches != [[ARCHIVE_SHA256, ARCHIVE]]:
        raise ValueError("Signed list must contain exactly the pinned filename and SHA256")


def verify_signature(directory=HERE / "verification"):
    directory = Path(directory)
    with tempfile.TemporaryDirectory(prefix="monero-verify-") as temporary:
        home = Path(temporary)
        gpg = ["gpg", "--no-options", "--homedir", str(home), "--batch", "--no-auto-key-retrieve"]
        subprocess.run(gpg + ["--import", str(directory / "binaryfate.asc")],
                       check=True, capture_output=True, timeout=20)
        keys = subprocess.run(gpg + ["--with-colons", "--fingerprint", "--list-keys"],
                              check=True, capture_output=True, text=True, timeout=20).stdout
        primary, expecting = [], False
        for line in keys.splitlines():
            fields = line.split(":")
            if fields[0] == "pub":
                expecting = True
            elif fields[0] == "fpr" and expecting:
                primary.append(fields[9]); expecting = False
        if primary != [FINGERPRINT]:
            raise ValueError("Unexpected release signing key fingerprint")
        verified = home / "verified-checksums.txt"
        process = subprocess.run(gpg + ["--status-fd=1", "--output", str(verified), "--decrypt",
                                       str(directory / "hashes-v0.18.5.1.asc")],
                                 check=True, capture_output=True, text=True, timeout=20)
        signatures = [line.split() for line in process.stdout.splitlines()
                      if line.startswith("[GNUPG:] VALIDSIG ")]
        if len(signatures) != 1 or signatures[0][-1] != FINGERPRINT:
            raise ValueError("Missing valid signature from the pinned primary key")
        validate_signed_checksum(verified.read_text())
        return {"version": VERSION, "source_commit": SOURCE_COMMIT, "archive": ARCHIVE,
                "archive_url": ARCHIVE_URL, "archive_sha256": ARCHIVE_SHA256,
                "signer_fingerprint": FINGERPRINT, "signature_valid": True,
                "signed_manifest_sha256": sha256(directory / "hashes-v0.18.5.1.asc"),
                "signing_key_file_sha256": sha256(directory / "binaryfate.asc")}


def verify_archive(path, directory=HERE / "verification"):
    result = verify_signature(directory)
    if sha256(path) != ARCHIVE_SHA256:
        raise ValueError("Downloaded archive SHA256 does not match the verified release")
    return {**result, "archive_verified": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_archive(args.archive), indent=2))
