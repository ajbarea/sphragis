import os, sys, ray, torch
mode = sys.argv[1]
print("DRIVER omp:", os.environ.get("OMP_NUM_THREADS"), "torch:", torch.get_num_threads(), flush=True)
kw = dict(num_cpus=4, include_dashboard=False, ignore_reinit_error=True)
if mode == "runtime_env":
    kw["runtime_env"] = {"env_vars": {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}}
else:
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[v] = "1"
ray.init(**kw)
@ray.remote(num_cpus=1)
class A:
    def probe(self):
        import os, torch
        return ("ACTOR omp:", os.environ.get("OMP_NUM_THREADS"), "torch:", torch.get_num_threads())
print(mode, *ray.get(A.remote().probe.remote()), flush=True)
print("DRIVER after:", torch.get_num_threads(), flush=True)
ray.shutdown()
