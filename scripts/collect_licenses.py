"""Copy installed runtime license texts into an onedir distribution."""

import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path


def collect(target):
    target = Path(target)
    for name in (
        "PySide6_Essentials",
        "shiboken6",
        "openpyxl",
        "et_xmlfile",
        "defusedxml",
        "regex",
    ):
        dist = distribution(name)
        count = 0
        for item in dist.files or ():
            if not any(word in str(item).lower() for word in ("license", "copying", "notice")):
                continue
            source = Path(dist.locate_file(item))
            if not source.is_file():
                continue
            relative = Path(*[part for part in item.parts if part not in ("..", ".")])
            destination = target / name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            count += 1
        print(f"{name}: {count} license/notice files")


if __name__ == "__main__":
    collect(sys.argv[1])
