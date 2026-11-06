"""Delete generated JSON under data/output and data/assessments, keeping inputs."""

from pathlib import Path


def clear_generated_json(project_dir):
    """Remove JSON files only; keep folders, .gitkeep and all other project data."""
    project_dir = project_dir.resolve()
    deleted = 0
    for name in ("output", "assessments"):
        folder = project_dir / "data" / name
        if not folder.exists():
            continue
        # Refuse redirected folders/files pointing outside the intended directory.
        if folder.is_symlink() or folder.resolve() != folder.absolute():
            raise ValueError("Refusing a redirected output folder: " + str(folder))
        for path in folder.rglob("*.json"):
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError("Refusing a file outside the output folder: " + str(path))
            path.unlink()
            deleted += 1
            print("Deleted " + str(path.relative_to(project_dir)))
    print(f"Removed {deleted} generated JSON files. Inputs and local evaluation results were kept.")
    return deleted


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[1]
    clear_generated_json(project)
