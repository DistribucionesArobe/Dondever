"""
Descarga datos de Google Search Console: últimos 28 días vs los 28 previos.

Setup (una sola vez):
  1. Google Cloud Console → crea un proyecto → habilita "Google Search Console API".
  2. Crea una Service Account → descarga su llave JSON.
  3. En Search Console, en cada propiedad → Configuración → Usuarios y permisos →
     agrega el email de la service account (permiso Completo o Restringido basta).
  4. pip install google-api-python-client google-auth
  5. export GSC_KEY=/ruta/a/llave.json

Uso:
  python scripts/gsc_pull.py --site sc-domain:dondever.app --out gsc/
  (Para propiedades de prefijo de URL usa: --site https://www.ejemplo.com/)
"""
import argparse, csv, os, sys
from datetime import date, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
LAG = 3  # GSC tiene ~2-3 días de retraso


def fetch(svc, site, start, end, dims):
    rows, start_row = [], 0
    while True:
        body = {"startDate": str(start), "endDate": str(end), "dimensions": dims,
                "rowLimit": 25000, "startRow": start_row}
        resp = svc.searchanalytics().query(siteUrl=site, body=body).execute()
        batch = resp.get("rows", [])
        rows += batch
        if len(batch) < 25000:
            return rows
        start_row += 25000


def write(path, rows, dims):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(dims + ["clicks", "impressions", "ctr", "position"])
        for r in rows:
            w.writerow(r["keys"] + [r["clicks"], r["impressions"],
                                    round(r["ctr"] * 100, 2), round(r["position"], 1)])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--site", required=True)
    p.add_argument("--out", default="gsc")
    a = p.parse_args()

    key = os.environ.get("GSC_KEY")
    if not key or not os.path.exists(key):
        sys.exit("ERROR: define GSC_KEY con la ruta a la llave JSON de la service account.")
    creds = service_account.Credentials.from_service_account_file(key, scopes=SCOPES)
    svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    end = date.today() - timedelta(days=LAG)
    start = end - timedelta(days=27)
    p_end = start - timedelta(days=1)
    p_start = p_end - timedelta(days=27)
    os.makedirs(a.out, exist_ok=True)

    qp = ["query", "page"]
    write(f"{a.out}/actual.csv", fetch(svc, a.site, start, end, qp), qp)
    write(f"{a.out}/anterior.csv", fetch(svc, a.site, p_start, p_end, qp), qp)

    cur = {r["keys"][0]: r for r in fetch(svc, a.site, start, end, ["page"])}
    prev = {r["keys"][0]: r for r in fetch(svc, a.site, p_start, p_end, ["page"])}
    with open(f"{a.out}/paginas.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["page", "clicks", "clicks_ant", "impr", "impr_ant", "pos", "pos_ant"])
        for page in sorted(set(cur) | set(prev)):
            c, pr = cur.get(page, {}), prev.get(page, {})
            w.writerow([page, c.get("clicks", 0), pr.get("clicks", 0),
                        c.get("impressions", 0), pr.get("impressions", 0),
                        round(c["position"], 1) if c else "", round(pr["position"], 1) if pr else ""])

    print(f"OK {a.site}: {start}→{end} vs {p_start}→{p_end}. Archivos en {a.out}/")


if __name__ == "__main__":
    main()
