from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    report = root / "test-results.txt"

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-v",
        "--tb=long",
        "--color=no",
        *sys.argv[1:],
    ]

    with report.open("w", encoding="utf-8") as output:
        result = subprocess.run(
            command,
            cwd=root,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )

    print(f"Test report: {report}")
    print(f"Exit code: {result.returncode}")

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
