"""Typer app for the screenredact command line.

New subcommands should be registered on the same `app` so users invoke them as
`screenredact <subcommand> ...`. Keep command bodies thin — push real logic
into library modules (e.g. `screenredact.detector`).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.progress import Progress

from screenredact.report import FrameAnalyzerReport

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Screen recording PII redaction tools.",
)


@app.callback()
def _root() -> None:
    # Registering a callback forces Typer into multi-command mode even while
    # only one subcommand exists, so users always invoke `screenredact <cmd>`.
    pass


@app.command()
def detect(
    frames_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Directory containing PNG frames (e.g. .<video>_frames/).",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Where to write JSON sidecars. Defaults to frames_dir.",
    ),
    lang: str = typer.Option(
        "en-US", "--lang", "-l", help="OCR language (BCP-47, e.g. en-US, de-DE)."
    ),
) -> None:
    # Lazy import: pulling in FrameAnalyzer transitively imports ocrmac (Apple
    # Vision bridge) + Pillow + presidio. Keeping it inside the command body
    # means lightweight commands like `screenredact report` work with just
    # `poetry install --without runtime`.
    from screenredact.detector import FrameAnalyzer

    out = output_dir or frames_dir
    out.mkdir(parents=True, exist_ok=True)

    frames = sorted(frames_dir.glob("*.png"))
    if not frames:
        typer.echo(f"No PNG frames found in {frames_dir}", err=True)
        raise typer.Exit(1)

    written = 0
    with Progress() as progress:
        task = progress.add_task("Analyzing frames", total=len(frames))
        for frame in frames:
            analyzer = FrameAnalyzer(lang=lang)
            analyzer.analyze_frame(frame)
            if analyzer.write_detections(frame, out):
                written += 1
            progress.update(task, advance=1)

    typer.echo(f"Analyzed {len(frames)} frame(s). Wrote {written} detection file(s) to {out}/")


@app.command()
def report(
    frames_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Directory containing detection sidecar JSONs (e.g. .<video>_frames/).",
    ),
) -> None:
    """Aggregate detection sidecars into a report.json roll-up."""
    r = FrameAnalyzerReport(frames_dir)
    r.scan()
    out = r.write()
    typer.echo(f"Wrote {out}")
    typer.echo(f"{r.frames_with_detections}/{r.total_frames} frames had detections.")
    if r.detection_counts:
        for t, n in r.detection_counts.most_common():
            typer.echo(f"{n:4d}  {t}")


@app.command()
def blur(
    frames_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Directory containing PNG frames and their detection sidecars.",
    ),
    padding: int = typer.Option(
        4,
        "--padding",
        "-p",
        help="Pixels of padding around each bbox to catch anti-aliased edges.",
    ),
) -> None:
    """Blur detected PII regions, writing `<stem>_blurred.png` siblings."""
    # Lazy import: FrameBlurrer pulls in cv2 from the `runtime` Poetry group.
    # Matching detect()'s pattern keeps lightweight commands (report) usable
    # with `poetry install --without runtime`.
    from screenredact.blurrer import FrameBlurrer

    frames = sorted(p for p in frames_dir.glob("*.png") if not p.stem.endswith("_blurred"))
    if not frames:
        typer.echo(f"No PNG frames found in {frames_dir}", err=True)
        raise typer.Exit(1)

    blurrer = FrameBlurrer(padding=padding)
    blurred = 0
    with Progress() as progress:
        task = progress.add_task("Blurring frames", total=len(frames))
        for frame in frames:
            sidecar = frames_dir / f"{frame.stem}.json"
            progress.update(task, advance=1)
            if not sidecar.exists():
                continue
            try:
                payload = json.loads(sidecar.read_text())
            except json.JSONDecodeError:
                continue
            detections = payload.get("detections") if isinstance(payload, dict) else None
            if not detections:
                continue
            output_path = frames_dir / f"{frame.stem}_blurred.png"
            blurrer.blur_and_write(frame, detections, output_path)
            blurred += 1

    typer.echo(f"Blurred {blurred}/{len(frames)} frame(s). Outputs at {frames_dir}/*_blurred.png")


if __name__ == "__main__":
    app()
