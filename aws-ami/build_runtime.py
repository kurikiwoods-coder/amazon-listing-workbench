"""Bundle the built workbench runtime for the Packer file provisioner."""

from pathlib import Path
from tarfile import open as open_tar


def build() -> Path:
    root = Path(__file__).resolve().parents[1]
    built = root / "frontend" / "out"
    web = built if (built / "index.html").is_file() else root / "web"
    if "V1.3.2" not in (web / "index.html").read_text(encoding="utf-8"):
        raise ValueError("Build the V1.3.2 frontend into web/ before packaging.")
    bundle = root / "aws-ami" / "workbench-runtime.tar.gz"
    files = [root / "requirements.txt", root / "backend" / "rules.json", *sorted((root / "backend").glob("*.py"))]
    with open_tar(bundle, "w:gz", compresslevel=9) as output:
        for path in files:
            output.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        for path in sorted(web.rglob("*")):
            relative = path.relative_to(web)
            if path.is_file() and not any(part.startswith(".") for part in relative.parts):
                output.add(path, arcname=(Path("web") / relative).as_posix(), recursive=False)
    return bundle


if __name__ == "__main__":
    print(build())
