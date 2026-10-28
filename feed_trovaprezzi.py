"""Feed Trovaprezzi per licenzeprogrammi.com, letto dall'Admin API di Shopify.

Struttura identica al feed Prestalia approvato da Trovaprezzi: 14 campi separati da "|",
nessuna intestazione, nessun <endrecord>. NON cambiare l'ordine dei campi senza avvisare
Trovaprezzi: al primo aggiornamento le offerte andrebbero offline.

Variabili d'ambiente:
  SHOPIFY_STORE          es. licenzeprogrammi.myshopify.com
  SHOPIFY_CLIENT_ID      app creata nel Dev Dashboard (permessi: read_products, read_inventory)
  SHOPIFY_CLIENT_SECRET
  oppure SHOPIFY_TOKEN   token di una vecchia custom app (shpat_...), se ne hai una

Uso: python feed_trovaprezzi.py [file_output]
"""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

DOMINIO = "https://licenzeprogrammi.com"
API_VERSION = "2026-07"
# Il "prezzo confronta" di Shopify NON è il prezzo minimo degli ultimi 30 giorni richiesto
# dalla Direttiva Omnibus: lasciarlo a False salvo decisione esplicita.
USA_PREZZO_RIFERIMENTO = False

QUERY = """
query($cursor: String) {
  products(first: 50, after: $cursor, query: "status:active") {
    pageInfo { hasNextPage endCursor }
    nodes {
      title handle vendor descriptionHtml onlineStoreUrl
      featuredImage { url }
      collections(first: 50) { nodes { title } }
      variants(first: 100) {
        nodes {
          id title sku barcode price compareAtPrice inventoryQuantity
          inventoryItem { tracked }
          image { url }
        }
      }
    }
  }
}
"""


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def access_token(store):
    if os.environ.get("SHOPIFY_TOKEN"):
        return os.environ["SHOPIFY_TOKEN"]
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": os.environ["SHOPIFY_CLIENT_ID"],
        "client_secret": os.environ["SHOPIFY_CLIENT_SECRET"],
    }).encode()
    return _post(f"https://{store}/admin/oauth/access_token", data,
                 {"Content-Type": "application/x-www-form-urlencoded"})["access_token"]


def scarica_prodotti(store, token):
    url = f"https://{store}/admin/api/{API_VERSION}/graphql.json"
    headers = {"Content-Type": "application/json", "X-Shopify-Access-Token": token}
    cursor, prodotti = None, []
    while True:
        r = _post(url, json.dumps({"query": QUERY, "variables": {"cursor": cursor}}).encode(), headers)
        if "errors" in r:
            raise SystemExit(f"Errore API Shopify: {r['errors']}")
        pagina = r["data"]["products"]
        prodotti += pagina["nodes"]
        if not pagina["pageInfo"]["hasNextPage"]:
            return prodotti
        cursor = pagina["pageInfo"]["endCursor"]


def testo(s):
    """Testo pulito per un campo: niente separatori, virgolette o a capo."""
    s = re.sub(r"\s+", " ", s or "").strip()
    return s.replace("|", "-").replace('"', "'")


def descrizione(html_src):
    s = re.sub(r"<[^>]+>", " ", html_src or "")
    s = testo(html.unescape(s))
    if len(s) > 255:  # limite della guida Trovaprezzi
        s = s[:255].rsplit(" ", 1)[0]
    return s


def prezzo(v):
    return f"{float(v):.2f}"


def righe(prodotti):
    out, saltati = [], []
    for p in prodotti:
        if not p["onlineStoreUrl"]:
            saltati.append((p["title"], "non pubblicato sul negozio online"))
            continue
        categorie = ">".join(sorted(testo(c["title"]) for c in p["collections"]["nodes"]))
        varianti = p["variants"]["nodes"]
        multi = len(varianti) > 1
        for v in varianti:
            nome = p["title"] + (f" - {v['title']}" if multi else "")
            if not v["sku"]:
                saltati.append((nome, "SKU mancante"))
                continue
            if float(v["price"]) <= 0:
                saltati.append((nome, "prezzo zero"))
                continue
            link = f"{DOMINIO}/products/{p['handle']}"
            if multi:
                link += "?variant=" + v["id"].rsplit("/", 1)[-1]
            if v["inventoryItem"]["tracked"]:
                disponibilita = str(max(0, v["inventoryQuantity"] or 0))
            else:
                disponibilita = "disponibile"
            img = (v["image"] or p["featuredImage"] or {}).get("url", "").split("?")[0]
            rif = ""
            if USA_PREZZO_RIFERIMENTO and v["compareAtPrice"] and float(v["compareAtPrice"]) > float(v["price"]):
                rif = prezzo(v["compareAtPrice"])
            out.append([
                f'"{testo(nome)}"',          # 1  Nome
                f'"{testo(p["vendor"])}"',   # 2  Marca
                "",                          # 3  vuoto (come nel feed Prestalia approvato)
                f'"{descrizione(p["descriptionHtml"])}"',  # 4 Descrizione
                rif,                         # 5  Prezzo di riferimento
                prezzo(v["price"]),          # 6  Prezzo vendita
                testo(v["sku"]),             # 7  Codice interno
                link,                        # 8  Link all'offerta
                disponibilita,               # 9  Disponibilità
                f'"{categorie}"',            # 10 Albero categorie
                img,                         # 11 Link immagine
                "0.0",                       # 12 Spese di spedizione (licenze ESD)
                testo(v["sku"]),             # 13 Codice produttore
                testo(v["barcode"]),         # 14 EAN
            ])
    return out, saltati


def main():
    store = os.environ["SHOPIFY_STORE"]
    output = sys.argv[1] if len(sys.argv) > 1 else "trovaprezzi.csv"
    prodotti = scarica_prodotti(store, access_token(store))
    out, saltati = righe(prodotti)
    if not out:  # un feed vuoto manderebbe offline tutte le offerte: meglio fallire
        raise SystemExit("Nessuna offerta generata: feed non scritto.")
    with open(output, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join("|".join(r) + "\n" for r in out))
    print(f"Prodotti attivi letti: {len(prodotti)}  Offerte nel feed: {len(out)}  Saltati: {len(saltati)}")
    for nome, motivo in saltati:
        print(f"  saltato: {nome} ({motivo})")


if __name__ == "__main__":
    main()
