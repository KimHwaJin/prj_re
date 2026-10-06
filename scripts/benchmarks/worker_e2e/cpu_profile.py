"""Diagnostic cProfile with thread CPU clocks, separately from speed trials.

Coroutine suspension and socket/DB wall waits are not CPU time. Main-thread
profiles and each offloaded ThreadPoolExecutor job use their own CPU clock.
Profile overhead is measured through matched on/off runs, never subtracted.
No argument values, prompts, locals or request bodies are collected.
"""

import cProfile
from concurrent.futures import ThreadPoolExecutor
import pstats
import time


class CPUProfile:
    def __init__(self, enabled):
        self.enabled = enabled
        self.main = None
        self.jobs = []
        self.result = None
        self.submit = ThreadPoolExecutor.submit

        def submit(pool, fn, *args, **kwargs):
            measured = (
                self.main is not None and self.result is None and enabled()
            )
            if not measured:
                return self.submit(pool, fn, *args, **kwargs)

            def job():
                profile = cProfile.Profile(timer=time.thread_time)
                began = time.thread_time()
                profile.enable()
                try:
                    return fn(*args, **kwargs)
                finally:
                    profile.disable()
                    self.jobs.append((profile, time.thread_time() - began))

            return self.submit(pool, job)

        ThreadPoolExecutor.submit = submit

    def start(self):
        if self.main is not None:
            raise RuntimeError("Profile reset is not supported during a trial")
        self.process_start = time.process_time()
        self.thread_start = time.thread_time()
        self.wall_start = time.perf_counter()
        self.main = cProfile.Profile(timer=time.thread_time)
        self.main.enable()

    def finish(self):
        if self.main is None:
            return None
        if self.result is not None:
            return self.result
        self.main.disable()
        process = time.process_time() - self.process_start
        main_cpu = time.thread_time() - self.thread_start
        jobs = list(self.jobs)
        wall = time.perf_counter() - self.wall_start
        export_start = time.process_time()

        def stats(profiles):
            total = pstats.Stats()
            for profile in profiles:
                total.add(profile)
            rows = []
            for (file, line, name), (
                primitive,
                calls,
                self_cpu,
                cumulative,
                callers,
            ) in total.stats.items():
                # pstats caller edges explain how a hot function was reached.
                # Export only function locations/counts/clocks, never arguments.
                # Inclusive edge clocks overlap and must not be summed as CPU.
                edges = []
                for (
                    parent_file,
                    parent_line,
                    parent_name,
                ), metrics in callers.items():
                    (
                        parent_calls,
                        parent_primitive,
                        parent_self,
                        parent_cumulative,
                    ) = metrics
                    edges.append(
                        {
                            "file": parent_file,
                            "line": parent_line,
                            "function": parent_name,
                            "calls": parent_calls,
                            "primitive_calls": parent_primitive,
                            "self_cpu_seconds": parent_self,
                            "cumulative_cpu_seconds": parent_cumulative,
                        }
                    )
                rows.append(
                    {
                        "file": file,
                        "line": line,
                        "function": name,
                        "primitive_calls": primitive,
                        "calls": calls,
                        "self_cpu_seconds": self_cpu,
                        "cumulative_cpu_seconds": cumulative,
                        "callers": sorted(
                            edges,
                            key=lambda edge: (
                                edge["file"],
                                edge["line"],
                                edge["function"],
                            ),
                        ),
                    }
                )
            return sorted(
                rows, key=lambda row: row["self_cpu_seconds"], reverse=True
            )

        self.result = {
            "active": False,
            "timer": "time.thread_time",
            "process_cpu_seconds": process,
            "main_thread_cpu_seconds": main_cpu,
            "offload_thread_cpu_seconds": sum(cpu for _, cpu in jobs),
            "profile_wall_seconds": wall,
            "offload_jobs": len(jobs),
            "main": stats([self.main]),
            "offload": stats([profile for profile, _ in jobs]),
        }
        self.result["export_cpu_seconds"] = time.process_time() - export_start
        return self.result
