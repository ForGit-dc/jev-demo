#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Serveur local de la demo Jev.

Deux raisons d'exister, et pas une de plus :

  1. L'API TypeSafe ne renvoie pas d'en-tete Access-Control-Allow-Origin. Un fetch
     lance depuis la page serait donc bloque par le navigateur. Ce script relaie.
  2. La cle ne doit pas se trouver dans le HTML. Elle est lue ici, cote serveur,
     et ne traverse jamais le navigateur.

Lancer, indifferemment :

    Windows      python serveur.py
    WSL / Linux  python3 serveur.py

Puis ouvrir http://localhost:8765

Sans ce serveur, index.html s'ouvre tel quel et reste entierement consultable :
toutes les reponses affichees ont ete enregistrees depuis la vraie API. Seul le
bouton "Demander a Jev" sur un texte modifie a besoin du direct.
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("PORT", 8765))
RACINE = Path(__file__).resolve().parent
API = "https://api.typesafe.ai/v1/systemone"
MODELE = "jev-latest"
WINDOWS = os.name == "nt"

# Les depots ou la cle peut vivre, relativement a un repertoire personnel.
DEPOTS = ("hate-speech-detection/.env", "social-data-studio/.env")


def distros_wsl():
    """Les distributions WSL visibles depuis Windows. Vide ailleurs."""
    if not WINDOWS:
        return []
    try:
        r = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    brut = r.stdout.decode("utf-16-le", "ignore") or r.stdout.decode("utf-8", "ignore")
    return [d.strip() for d in brut.replace("\x00", "").splitlines()
            if d.strip() and "docker" not in d.strip().lower()]


def candidats():
    """Tous les .env a essayer, du plus proche au plus lointain."""
    vus, sortie = set(), []

    def ajoute(p):
        s = str(p)
        if s not in vus:
            vus.add(s)
            sortie.append(s)

    ajoute(RACINE / ".env")                       # pose a cote du script
    for d in DEPOTS:                              # depuis Linux ou WSL
        ajoute(Path.home() / d)
        ajoute(Path("/home/anass") / d)
    for distro in distros_wsl():                  # depuis Windows, a travers WSL
        for racine in (f"\\\\wsl.localhost\\{distro}", f"\\\\wsl$\\{distro}"):
            for d in DEPOTS:
                motif = f"{racine}\\home\\*\\" + d.replace("/", "\\")
                for trouve in sorted(glob.glob(motif)):
                    ajoute(trouve)
    return sortie


def lire_cle(chemin):
    try:
        with open(chemin, encoding="utf-8", errors="replace") as fh:
            for ligne in fh:
                nom, sep, val = ligne.partition("=")
                if sep and nom.strip().lower() in ("jev_anass", "typesafe_api_key"):
                    val = val.strip().strip('"').strip("'")
                    if val:
                        return val
    except OSError:
        pass
    return None


def trouver_cle():
    """Variable d'environnement d'abord, puis les .env connus. Jamais affichee."""
    for var in ("TYPESAFE_API_KEY", "jev_anass", "JEV_ANASS"):
        v = os.environ.get(var)
        if v and v.strip():
            return v.strip(), f"variable d'environnement {var}"
    essayes = candidats()
    for p in essayes:
        cle = lire_cle(p)
        if cle:
            return cle, p
    return None, essayes


CLE, ORIGINE = trouver_cle()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(RACINE), **kw)

    def log_message(self, fmt, *args):
        pass  # silence : rien qui puisse contenir un secret ne doit finir dans un log

    def _rep(self, code, obj):
        corps = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self):
        if self.path.startswith("/api/ping"):
            return self._rep(200 if CLE else 503, {"ok": bool(CLE), "modele": MODELE})
        return super().do_GET()

    def do_POST(self):
        if not self.path.startswith("/api/jev"):
            return self._rep(404, {"erreur": "route inconnue"})
        if not CLE:
            return self._rep(503, {"erreur": "cle jev_anass introuvable cote serveur"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            entree = json.loads(self.rfile.read(n) or b"{}")
            corps = json.dumps({"model": entree.get("model", MODELE),
                                "state": entree["state"],
                                "questions": entree["questions"]}).encode()
        except Exception as e:
            return self._rep(400, {"erreur": f"requete illisible : {e}"})

        req = urllib.request.Request(API, data=corps, headers={
            "Authorization": f"Bearer {CLE}", "Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return self._rep(e.code, {"erreur": f"API TypeSafe {e.code}",
                                      "detail": e.read().decode()[:400]})
        except Exception as e:
            return self._rep(502, {"erreur": f"appel impossible : {e}"})
        out["_ms"] = round((time.perf_counter() - t0) * 1000)
        return self._rep(200, out)


def ouvrir(url):
    """Windows et Linux savent faire. WSL non : passer la main a Windows."""
    if not WINDOWS and hasattr(os, "uname") and "microsoft" in os.uname().release.lower():
        for cmd in (["wslview", url], ["explorer.exe", url]):
            try:
                subprocess.run(cmd, check=False, timeout=5,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except (OSError, subprocess.SubprocessError):
                continue
        return
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    if not (RACINE / "index.html").exists():
        sys.exit(f"index.html introuvable dans {RACINE}")

    print(f"\n  Demo Jev  ->  http://localhost:{PORT}")
    if CLE:
        print(f"  Cle lue depuis : {ORIGINE}")
        print("  Elle reste cote serveur, le navigateur ne la voit jamais.")
        print("  Mode direct actif : le bac a sable interroge vraiment l'API.\n")
    else:
        print("\n  Cle jev_anass INTROUVABLE.")
        print("  La page fonctionne quand meme, sur les reponses enregistrees.")
        print("\n  Pour activer le direct, au choix :")
        if WINDOWS:
            print(f'    set jev_anass=<la cle>  puis  python "{Path(__file__).name}"')
            print("    ou lancer depuis WSL :  python3 serveur.py")
        else:
            print(f'    export jev_anass=<la cle>  puis  python3 {Path(__file__).name}')
        print("\n  Cherchee dans :")
        for p in (ORIGINE or [])[:10]:
            print(f"    {p}")
        if not ORIGINE:
            print("    (aucun chemin candidat)")
        print()

    ouvrir(f"http://localhost:{PORT}")
    try:
        HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n  Arret.\n")
