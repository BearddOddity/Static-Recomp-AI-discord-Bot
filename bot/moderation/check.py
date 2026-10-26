"""Run the moderation check on local files, without Discord.

    python -m bot.moderation.check examples/scam/*.webp
    python -m bot.moderation.check --text "free nitro at dlscord-gift.com"
    python -m bot.moderation.check --rules-only examples/scam/*.webp
    python -m bot.moderation.check --rules-only some-upload.zip

All files given together are treated as one message with that many attachments.
Images are shown to the AI; every file name (and .zip contents) is checked against
the anti-piracy rules. Uses the AI settings from .env (MOD_AI_MODEL if set).
"""

import argparse
import asyncio
import mimetypes
import sys
from pathlib import Path

from ..ai import Image, build_provider
from ..config import load_settings
from .classifier import Classifier
from .engine import Moderator, Post
from .files import MAX_ZIP_BYTES, zip_member_names
from .links import LinkChecker


async def run(args: argparse.Namespace) -> int:
    settings = load_settings(require_discord=False, require_ai=not args.rules_only)
    images, names = [], []
    for path in args.images:
        file = Path(path)
        names.append(file.name)
        media_type = mimetypes.guess_type(path)[0] or ""
        if media_type.startswith("image/"):
            images.append(Image(file.read_bytes(), media_type))
        elif file.suffix.lower() == ".zip" and file.stat().st_size <= MAX_ZIP_BYTES:
            names += zip_member_names(file.name, file.read_bytes())

    provider = None if args.rules_only else build_provider(settings, model=settings.mod_ai_model)
    moderator = Moderator(
        LinkChecker(settings.mod_blocked_domains, settings.mod_allowed_domains, settings.mod_piracy_domains),
        Classifier(provider) if provider else None,
        ai_scan="off" if args.rules_only else "all",
        min_confidence=settings.mod_min_confidence,
    )
    post = Post(text=args.text, author_id=0, channel_id=0, images=images,
                attachment_count=len(args.images), attachment_names=names)
    try:
        verdict = await moderator.review(post)
    finally:
        if provider:
            await provider.aclose()

    if provider and not provider.supports_images and images:
        print(f"note: {provider.name} can't see images, so only the text was judged")
    print(f"verdict:    {verdict.label}")
    print(f"confidence: {verdict.confidence:.2f}")
    print(f"decided by: {verdict.source} (rule score {verdict.score})")
    for reason in verdict.reasons:
        print(f"  - {reason}")
    return 1 if verdict.flagged else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="*", metavar="files", help="files to check as one message's attachments")
    parser.add_argument("--text", default="", help="message text")
    parser.add_argument("--rules-only", action="store_true", help="skip the AI and use only the built-in rules")
    args = parser.parse_args()
    if not args.images and not args.text:
        parser.error("give files, --text, or both")
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
