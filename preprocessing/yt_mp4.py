#!/usr/bin/env python3
"""
Usage:
  python preprocessing/yt_mp4.py --link "<youtube_url>"

Environment:
  YT_DLP_BROWSER=chrome|safari|brave|edge|firefox|chromium
    Prefer a specific browser-cookie source for fallback attempts.
"""

import argparse
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


COOKIE_BROWSER_PATHS = {
    "chrome": Path.home() / "Library/Application Support/Google/Chrome",
    "brave": Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser",
    "edge": Path.home() / "Library/Application Support/Microsoft Edge",
    "chromium": Path.home() / "Library/Application Support/Chromium",
    "firefox": Path.home() / "Library/Application Support/Firefox",
    "safari": Path("/Applications/Safari.app"),
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/134.0.0.0 Safari/537.36"
)


def available_cookie_browsers():
    preferred = str(os.environ.get("YT_DLP_BROWSER") or "").strip().lower()
    browsers = []

    if preferred:
        if preferred not in COOKIE_BROWSER_PATHS:
            raise SystemExit(
                "Unsupported YT_DLP_BROWSER={!r}. Choose from: {}".format(
                    preferred,
                    ", ".join(sorted(COOKIE_BROWSER_PATHS)),
                )
            )
        browsers.append(preferred)

    for browser_name in ("chrome", "brave", "edge", "safari", "firefox", "chromium"):
        if browser_name in browsers:
            continue
        if COOKIE_BROWSER_PATHS[browser_name].exists():
            browsers.append(browser_name)

    return browsers


def build_attempts(output_template):
    common_opts = {
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "overwrites": True,
        "noplaylist": True,
        "geo_bypass": True,
        "http_headers": {
            "User-Agent": USER_AGENT,
        },
    }

    attempts = [
        {
            "label": "direct-progressive-mp4",
            **common_opts,
            "format": "b[ext=mp4]/b/best",
        },
        {
            "label": "direct-merged-best",
            **common_opts,
            "format": "bv*+ba/b",
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web", "ios"],
                }
            },
        },
    ]

    for browser_name in available_cookie_browsers():
        attempts.append(
            {
                "label": "cookies-" + browser_name,
                **common_opts,
                "format": "b[ext=mp4]/bv*+ba/b",
                "cookiesfrombrowser": (browser_name,),
                "extractor_args": {
                    "youtube": {
                        "player_client": ["android", "web", "ios"],
                    }
                },
            }
        )

    return attempts


def yt_dlp_backend():
    try:
        import yt_dlp  # type: ignore
    except ModuleNotFoundError:
        cli_path = shutil.which("yt-dlp")
        if cli_path:
            return ("cli", cli_path)
        raise SystemExit(
            "yt-dlp is not available in this environment.\n"
            "The Python module 'yt_dlp' is not installed and the 'yt-dlp' executable is not on PATH.\n"
            "Install one of:\n"
            "  python3 -m pip install yt-dlp\n"
            "  brew install yt-dlp"
        ) from None

    return ("python", yt_dlp)


def cli_args_for_attempt(cli_path: str, attempt: dict[str, Any], link: str) -> list[str]:
    args = [
        cli_path,
        "--ignore-config",
        "--force-overwrites",
        "--no-playlist",
        "--geo-bypass",
        "--user-agent",
        USER_AGENT,
        "--merge-output-format",
        str(attempt["merge_output_format"]),
        "--output",
        str(attempt["outtmpl"]),
        "--format",
        str(attempt["format"]),
    ]

    extractor_args = attempt.get("extractor_args") or {}
    youtube_args = extractor_args.get("youtube") or {}
    player_clients = youtube_args.get("player_client") or []
    if player_clients:
        args.extend(
            [
                "--extractor-args",
                "youtube:player_client={}".format(",".join(str(client) for client in player_clients)),
            ]
        )

    cookies_from_browser = attempt.get("cookiesfrombrowser") or ()
    if cookies_from_browser:
        args.extend(["--cookies-from-browser", str(cookies_from_browser[0])])

    args.append(link)
    return args


def summarize_process_output(stdout: str, stderr: str) -> str:
    lines = []
    for block in (stdout, stderr):
        for line in str(block or "").splitlines():
            clean = line.strip()
            if clean:
                lines.append(clean)
    return lines[-1] if lines else ""


def download_with_cli(cli_path: str, attempt: dict[str, Any], link: str) -> None:
    completed = subprocess.run(
        cli_args_for_attempt(cli_path, attempt, link),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return

    message = summarize_process_output(completed.stdout, completed.stderr)
    if not message:
        message = "yt-dlp exited with status {}".format(completed.returncode)
    raise RuntimeError(message)


def convert_yt_mp4(link, output_template="%(title)s.%(ext)s"):
    """Download link, retrying with browser cookies when YouTube blocks anonymous access."""
    backend_kind, backend = yt_dlp_backend()

    if shutil.which("ffmpeg") is None:
        print("[warn] ffmpeg not found on PATH. Merging video+audio may fail or produce video-only files.")
        print("       Install with: brew install ffmpeg")

    errors = []
    for attempt in build_attempts(output_template):
        label = str(attempt["label"])
        options = {key: value for key, value in attempt.items() if key != "label"}
        try:
            print("[yt_mp4] trying {} via {}".format(label, backend_kind))
            if backend_kind == "python":
                with backend.YoutubeDL(options) as ydl:
                    ydl.download([link])
            else:
                download_with_cli(str(backend), options, link)
            print("[yt_mp4] success via {} ({})".format(label, backend_kind))
            return
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or exc.__class__.__name__
            errors.append("{}: {}".format(label, message))
            print("[yt_mp4] failed via {}: {}".format(label, message))

    help_lines = [
        "All yt-dlp download attempts failed.",
        "Tried: {}".format("; ".join(errors) if errors else "no attempts were made"),
        "If this is an age-restricted, region-restricted, or bot-protected video, sign into YouTube in Chrome and retry.",
        "Optional: set YT_DLP_BROWSER=chrome before starting the bridge to force Chrome cookies.",
    ]
    raise SystemExit("\n".join(help_lines))


def main():
    parser = argparse.ArgumentParser(description="Download a YouTube video as MP4 (with audio).")
    parser.add_argument("--link", required=True, type=str, help="The URL of the YouTube video to download.")
    parser.add_argument("-o", "--output", default="%(title)s.%(ext)s", help="Output filename template.")
    args = parser.parse_args()

    convert_yt_mp4(args.link, output_template=args.output)


if __name__ == "__main__":
    main()
