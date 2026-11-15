#!/usr/bin/env python3
"""Ruteo en segundo plano: lo lanza la herramienta autoroute de librepcb_mcp.py.

Uso: route_worker.py <job.json>
Escribe el progreso en <workdir>/status.json (lo lee autoroute_status).
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import librepcb_route as R  # noqa: E402


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    workdir = Path(job["workdir"])
    status = workdir / "status.json"
    st = {"state": "running", "started": time.time(), "pid": os.getpid(), "log": [], "job": job}

    def save():
        tmp = status.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(status)

    def log(msg):
        st["log"].append(time.strftime("%H:%M:%S ") + msg)
        save()

    save()
    try:
        lpp, board = Path(job["lpp"]), Path(job["board"])
        res = R.route_project(lpp, board, Path(job["server_dir"]), workdir, job.get("opts", {}), log)
        st["result"] = res
        if res.get("ok") and job.get("apply"):
            li = R.lock_info(lpp.parent)
            if li["locked"] and li.get("alive") is not False:
                st["applied"] = False
                st["apply_msg"] = ("LibrePCB tiene el proyecto abierto: no escribí la placa. "
                                   "Ciérralo y usa apply_ses con el resultado.")
            else:
                ses = json.loads(Path(res["best_json"]).read_text(encoding="utf-8"))
                text, stats = R.apply_ses_to_board(lpp, board, ses)
                st["backup"] = R.backup_and_write(lpp, board, text)
                st["applied"] = True
                st["apply_stats"] = stats
                log(f"Pistas escritas en la placa ({stats['traces']} tramos, {stats['vias']} vías).")
        st["state"] = "done"
    except Exception:  # noqa: BLE001
        st["state"] = "error"
        st["error"] = traceback.format_exc()
    st["finished"] = time.time()
    save()


if __name__ == "__main__":
    main()
