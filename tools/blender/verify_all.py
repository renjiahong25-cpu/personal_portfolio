"""批量验证全部 21 个道具 spritesheet（逐个调 verify_sheet.py 并汇总）。

用法: python verify_all.py   （在 tools\\blender 目录运行；CWD 无关）
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAMES = [
    "cart", "tent", "grand_piano", "bonfire", "signal_tower", "server_rack",
    "satellite_dish", "container_r", "container_b", "container_g", "gantry",
    "ship", "scaffold", "crane", "fence", "key_tile", "tree_a", "tree_b",
    "rock", "sign", "dock",
]


def main():
    py = sys.executable
    results = []
    for n in NAMES:
        r = subprocess.run([py, os.path.join(ROOT, "verify_sheet.py"), n],
                           capture_output=True, text=True, cwd=ROOT, timeout=900)
        verdict = "NO_RESULT"
        for line in r.stdout.splitlines():
            if line.startswith("RESULT "):
                verdict = line.split()[1]
        if verdict != "PASS":
            print("---- %s ----" % n)
            print(r.stdout.strip())
            if r.stderr.strip():
                print("STDERR", r.stderr.strip())
        results.append((n, verdict))
    n_pass = sum(1 for _, v in results if v == "PASS")
    print("VERIFY_ALL_DONE %d/%d PASS" % (n_pass, len(results)))
    for n, v in results:
        print("  %-15s %s" % (n, v))


if __name__ == "__main__":
    main()
