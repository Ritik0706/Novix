from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = ROOT / "resources" / "llama"

RUNTIMES = {
    "cpu": RUNTIME_ROOT / "cpu" / "llama-b10818-bin-win-cpu-x64",
    "cuda": RUNTIME_ROOT / "cuda",
    "vulkan": RUNTIME_ROOT / "vulkan",
}


def check_runtime(name, directory):
    executable = directory / "llama-server.exe"

    print(f"\n[{name}]")
    print(f"directory: {directory}")
    print(f"executable: {executable}")

    if not directory.is_dir():
        print("FAIL: runtime directory missing")
        return False

    if not executable.is_file():
        print("FAIL: llama-server.exe missing")
        return False

    try:
        result = subprocess.run(
            [str(executable), "--help"],
            cwd=str(directory),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except OSError as exc:
        print(f"FAIL: executable could not start: {exc}")
        return False
    except subprocess.TimeoutExpired:
        print("FAIL: --help timed out")
        return False

    if result.returncode != 0:
        print(f"FAIL: --help returned {result.returncode}")
        if result.stderr:
            print(result.stderr)
        elif result.stdout:
            print(result.stdout)
        return False

    print("PASS: llama-server.exe --help")
    return True


def main():
    ok = True

    for name, directory in RUNTIMES.items():
        if not check_runtime(name, directory):
            ok = False

    if not ok:
        return 1

    print("\nAll runtime executable probes passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())