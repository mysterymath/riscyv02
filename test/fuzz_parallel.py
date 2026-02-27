#!/usr/bin/env python3
"""Parallel fuzz runner for RISCY-V02.

Launches multiple cocotb fuzz workers with non-overlapping seeds,
separate build dirs, and no VCD dumping.

Usage:
    python fuzz_parallel.py -j 16              # 16 workers, infinite
    python fuzz_parallel.py -j 32 --seed 5000  # custom start seed
    python fuzz_parallel.py -j 8 --iters 1000  # finite per worker
"""

import argparse
import os
import signal
import subprocess
import sys
import time

SEED_SPACING = 1_000_000


def main():
    parser = argparse.ArgumentParser(description="Parallel RISCY-V02 fuzz runner")
    parser.add_argument("-j", "--jobs", type=int, required=True, help="Number of workers")
    parser.add_argument("--seed", type=int, default=0, help="Starting seed (default: 0)")
    parser.add_argument("--iters", type=int, default=0, help="Iterations per worker (0=infinite)")
    args = parser.parse_args()

    procs = []
    for i in range(args.jobs):
        seed = args.seed + i * SEED_SPACING
        log = f"fuzz_{i}.log"
        env = {
            **os.environ,
            "FUZZ_SEED": str(seed),
            "FUZZ_ITERS": str(args.iters),
        }
        cmd = [
            "make",
            f"SIM_BUILD=sim_build/fuzz_{i}",
            "NODUMP=1",
            "COCOTB_TEST_MODULES=test_fuzz",
            f"COCOTB_RESULTS_FILE=results_fuzz_{i}.xml",
        ]
        with open(log, "w") as f:
            p = subprocess.Popen(
                cmd, env=env, stdout=f, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        procs.append((i, p, log))
        print(f"Worker {i}: seed={seed} pid={p.pid} log={log}")

    print(f"\n{args.jobs} workers running. Ctrl-C to stop all.\n")

    def kill_all():
        for _, p, _ in procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        while procs:
            time.sleep(1)
            still_running = []
            for i, p, log in procs:
                ret = p.poll()
                if ret is None:
                    still_running.append((i, p, log))
                elif ret != 0:
                    print(f"FAIL: Worker {i} exited with code {ret} — see {log}")
                else:
                    print(f"OK:   Worker {i} finished — see {log}")
            procs = still_running
    except KeyboardInterrupt:
        print("\nCtrl-C received, killing all workers...")
        kill_all()
    finally:
        # Clean up results files
        for i in range(args.jobs):
            f = f"results_fuzz_{i}.xml"
            if os.path.exists(f):
                os.remove(f)


if __name__ == "__main__":
    main()
