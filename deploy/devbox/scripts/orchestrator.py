*** Begin Patch
*** Update File: deploy/devbox/scripts/orchestrator.py
@@
-def _run(
-    cmd: list[str],
-    cwd: Path,
-    env: Optional[dict] = None,
-    timeout: int = 900,
-    stdin_data: Optional[str] = None,
-) -> subprocess.CompletedProcess:
+def _run(
+    cmd: list[str],
+    cwd: Path,
+    env: Optional[dict] = None,
+    timeout: int = 1200,
+    stdin_data: Optional[str] = None,
+) -> subprocess.CompletedProcess:
@@
-    return subprocess.run(
+    # Note: default timeout raised from 900s (15m) to 1200s (20m) to allow
+    # long-running deep-reasoning tasks to complete without being killed by
+    # a hard subprocess timeout. Callers may still pass an explicit timeout.
+    return subprocess.run(
         cmd,
         cwd=cwd,
         env=env if env is not None else None,
         input=stdin_data,
         capture_output=True,
         text=True,
         timeout=timeout,
         check=False,
     )
*** End Patch
