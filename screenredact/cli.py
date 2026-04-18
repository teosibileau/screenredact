"""Typer app for the screenredact command line.

New subcommands should be registered on the same `app` so users invoke them as
`screenredact <subcommand> ...`. Keep command bodies thin — push real logic
into library modules (e.g. `screenredact.detector`).
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.progress import Progress

from screenredact.detector import FrameAnalyzer

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
    lang: str = typer.Option("en", "--lang", "-l", help="OCR language code."),
) -> None:
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


if __name__ == "__main__":
    app()
