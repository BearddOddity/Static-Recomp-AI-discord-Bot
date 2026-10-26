"""Anti-piracy: game files shared as attachments, inside .zip files, or as direct links.

Server policy: no sharing game files of any kind (executables, disc images, ROMs,
packages, keys) and no links to pirated content. Talking about files, like
"my default.xex crashes", is fine; only sharing the files themselves is not.
"""

import io
import posixpath
import zipfile
from urllib.parse import unquote, urlsplit

from .signals import Signal

CERTAIN = 100  # same as a blocklisted domain: no AI needed

# Extension -> what it is, for the reason shown to members and moderators.
GAME_FILE_EXTENSIONS = {
    # Console executables
    ".elf": "console executable", ".xbe": "Xbox executable", ".xex": "Xbox 360 executable",
    ".self": "PS3 executable", ".sprx": "PS3 module", ".prx": "PlayStation module",
    # Disc images
    ".iso": "disc image", ".cso": "disc image", ".chd": "disc image", ".cue": "disc image",
    ".img": "disc image", ".mdf": "disc image", ".mds": "disc image", ".nrg": "disc image",
    ".ccd": "disc image", ".gdi": "disc image", ".cdi": "disc image", ".rvz": "disc image",
    ".wbfs": "disc image", ".wia": "disc image", ".gcm": "disc image", ".ecm": "disc image",
    ".pbp": "disc image", ".xiso": "disc image",
    # Console packages and keys
    ".pkg": "console package", ".nsp": "Switch package", ".nsz": "Switch package",
    ".xci": "Switch cartridge image", ".cia": "3DS package", ".vpk": "Vita package",
    ".rap": "PS3 license key", ".edat": "PS3 license file",
    # ROMs
    ".z64": "ROM", ".n64": "ROM", ".v64": "ROM", ".nes": "ROM", ".fds": "ROM", ".sfc": "ROM",
    ".smc": "ROM", ".gb": "ROM", ".gbc": "ROM", ".gba": "ROM", ".nds": "ROM", ".3ds": "ROM",
    ".gen": "ROM", ".md": None, ".sms": "ROM", ".gg": "ROM", ".32x": "ROM", ".pce": "ROM",
    ".a26": "ROM", ".a78": "ROM", ".lnx": "ROM", ".ws": "ROM", ".wsc": "ROM", ".ngp": "ROM",
    ".vb": None, ".rom": "ROM",
}
# ".md" (Markdown) and ".vb" (Visual Basic) are too common to treat as ROMs.
GAME_FILE_EXTENSIONS = {ext: kind for ext, kind in GAME_FILE_EXTENSIONS.items() if kind}

# Files whose name gives them away, whatever the extension.
GAME_FILE_NAMES = {"eboot.bin": "PS3 executable", "default.xex": "Xbox 360 executable", "default.xbe": "Xbox executable"}

MAX_ZIP_BYTES = 25 * 1024 * 1024


def game_file_kind(name: str) -> str | None:
    base = posixpath.basename(name.replace("\\", "/")).lower()
    if base in GAME_FILE_NAMES:
        return GAME_FILE_NAMES[base]
    return GAME_FILE_EXTENSIONS.get(posixpath.splitext(base)[1])


def check_attachments(names: list[str]) -> list[Signal]:
    """names are attachment file names, plus "archive.zip/inner/path" for zip contents."""
    signals = []
    lowered = [n.lower() for n in names]
    for name in names:
        kind = game_file_kind(name)
        if kind:
            signals.append(Signal(f"shared a game file: {posixpath.basename(name)} ({kind})", CERTAIN, "piracy"))
        elif name.lower().endswith(".bin"):
            # .bin is a disc image next to a .cue, otherwise often just data.
            if any(n.endswith(".cue") for n in lowered):
                signals.append(Signal(f"shared a game file: {posixpath.basename(name)} (disc image)", CERTAIN, "piracy"))
            else:
                signals.append(Signal(f"shared a .bin file that may be a disc image ({posixpath.basename(name)})", 20, "piracy"))
    return _dedupe(signals)


def check_urls(urls: list[str], is_trusted=lambda url: False) -> list[Signal]:
    """Direct links to game files on file hosts, the Discord CDN and so on. Trusted
    sites (GitHub releases of open source tools, for example) are skipped."""
    signals = []
    for url in urls:
        if is_trusted(url):
            continue
        path = unquote(urlsplit(url if "://" in url else "http://" + url).path)
        kind = game_file_kind(path) if path.strip("/") else None
        if kind:
            signals.append(Signal(f"link to a game file: {posixpath.basename(path)} ({kind})", CERTAIN, "piracy"))
    return _dedupe(signals)


def zip_member_names(filename: str, data: bytes) -> list[str]:
    """List what's inside a .zip, so game files can't hide in an archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return [f"{filename}/{info.filename}" for info in archive.infolist()[:500] if not info.is_dir()]
    except (zipfile.BadZipFile, ValueError, OSError):
        return []


def _dedupe(signals: list[Signal]) -> list[Signal]:
    return list({s.reason: s for s in signals}.values())
