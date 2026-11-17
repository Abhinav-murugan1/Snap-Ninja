"""Resolve clip destinations without moving job metadata or original media."""
from pathlib import Path
import tempfile


def clips_directory(job_dir: Path, output_dir: str = "") -> Path:
    """Keep legacy jobs in place; custom destinations get one folder per job."""
    return (Path(output_dir) / job_dir.name / "clips") if output_dir else job_dir / "clips"


def validate_destination(value: str, jobs_root: Path) -> str:
    value = value.strip().strip('"')
    if not value:
        return ""
    folder = Path(value).expanduser()
    if not folder.is_absolute():
        raise ValueError("Choose an absolute folder path, such as D:\\Snap Ninja exports.")
    folder = folder.resolve()
    internal = jobs_root.resolve()
    if folder == internal:
        return ""
    if internal in folder.parents:
        raise ValueError("Choose a folder outside Snap Ninja's jobs folder.")
    folder.mkdir(parents=True, exist_ok=True)
    # Check actual write access (including network shares and Windows ACLs).
    with tempfile.TemporaryFile(dir=folder) as probe:
        probe.write(b"Snap Ninja output check")
    return str(folder)
