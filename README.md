# bok_to_pef – FastAPI-mikrotjeneste

Konverterer XHTML fil til PEF

- **Output:** En oppdatert XHTML-fil (kan overskrive input eller skrives til ny sti).

## Mappestruktur

```
artifacts/
bok_to_pef/
  ├─ xslt/
  ├─ app.py               # FastAPI-wrapper
  ├─ bok_to_pef.py   # Din domene-modul (importeres av app.py)
  ├─ requirements.txt     # Python-avhengigheter
  ├─ Dockerfile           # Containeroppsett
  └─ README.md            # Denne fila
```

## Kjøremodeller

### 1) Docker (standalone)

```bash
# Bygg image
docker build -t bok_to_pef:dev ./modules/utils/bok_to_pef

# Kjør container
docker run --rm -p 7013:7013   -e MODULE_NAME=bok_to_pef   -v "$(pwd)/volumes:/data"   bok_to_pef:dev
```

### 2) docker compose (eksempel)

Legg til en tjeneste i `docker-compose.yml`:

```yaml
bok_to_pef:
  image:llsynit/bok_to_pef
  ports:
    - "7013:7013"

```

Start kun denne modulen:

```bash
docker compose up -d --build bok_to_pef
```

## API

Base-URL: `http://localhost:7013`

### `GET /health`

Helsetest.

**Respons on success **

```json
{
  "status": true,
  "module": "bok_to_pef",
  "production_number": "string",
  "filename": "result.zip",
  "download_url": "http://127.0.0.1:7013/download/xx",
  "duration_ms": 11
}
```

**Respons on failure**

```json
{
  "status": false,
  "module": "bok_to_pef",
  "production_number": "string",
  "filename": "result.zip",
  "download_url": "http://127.0.0.1:7013/download/xx",
  "duration_ms": 11
}
```

### `POST /run`

Kjører innsetting av metadata.

**Body**

```json
{
  "input": "863630.xhtml",
  "opf": "package.opf"
}
```

- `input` (påkrevd): sti til XHTML (relativ til `/data` eller absolutt).

**Respons (200)**

```json
{
  "module": "bok_to_pef",
  "input": "/data/in/863630.xhtml",
  "output": "/data/out/863630.xhtml",
  "production_number": "863630",
  "duration_ms": 152,
  "status": "ok"
}
```

**Feilkoder**

- `404`: Inputfil finnes ikke.
- `400`: Feil i innsetting (typisk nettverks-/parsingfeil i `bok_to_pef.py`).
- `500`: Uventet feil eller kunne ikke skrive til disk.

## Hurtigtest

```bash
mkdir -p volumes/in volumes/out

cat > volumes/in/sample.xhtml <<'HTML'
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Sample</title></head>
<body><p>Hei metadata!</p></body>
</html>
HTML

# Kjør
curl -X 'POST' \
  'http://127.0.0.1:7013/run' \
  -H 'accept: application/json' \
  -H 'Content-Type: multipart/form-data' \
  -F 'production_number=string' \
  -F 'xhtml=@863440.xhtml;type=application/xhtml+xml' \
  -F 'opf=@package.opf'
```

Se resultat:

## Konfigurasjon

Miljøvariabler (fornuftige defaults):

- `MODULE_NAME` – navn for helsesjekk og logging (default: `insert_metadata`).

- `PORT` – HTTP-port i container (default: `7003`).

## Avhengigheter

`requirements.txt`:

```
annotated-types==0.7.0
anyio==4.10.0
beautifulsoup4==4.13.5
certifi==2025.8.3
charset-normalizer==3.4.3
click==8.1.8
dotenv==0.9.9
exceptiongroup==1.3.0
fastapi==0.116.1
h11==0.16.0
httptools==0.6.4
idna==3.10
lxml==6.0.1
pydantic==2.11.7
pydantic_core==2.33.2
pymarc==5.3.1
python-dotenv==1.1.1
python-multipart==0.0.20
PyYAML==6.0.2
requests==2.32.5
sniffio==1.3.1
soupsieve==2.7
starlette==0.47.3
typing-inspection==0.4.1
typing_extensions==4.15.0
urllib3==2.5.0
uvicorn==0.35.0
uvloop==0.21.0
watchfiles==1.1.0
websockets==15.0.1

```

## Forventninger til `insert_metadata.py`

Appen forventer en funksjon med signatur:

```python
soup_or_str = update_html_head_with_marc(input_path: str, production_number: str)
```

- Returnerer enten en `BeautifulSoup`-instans eller ferdig serialisert `str`.
- Kaster unntak ved feil (nettverk, parsing, …) – dette mappes til `400 Bad Request`.

## Drift og feilsøking

- **Healthcheck:** `GET /health`
- **Nettverkskrav:** Hvis `insert_metadata.py` henter metadata over nett, må containeren ha utgående nettverkstilgang.
- **Tilkoblingsfeil:** Valider `production_number`, API-nøkler/tilganger (hvis relevant), og at eksternt API svarer.
- **Filrettigheter:** Sørg for at host-volumet gir skrive-/lesetilgang for container-brukeren.
- **Valider XHTML:** Det kan være lurt å kjøre en XHTML/EPUB-validering i neste steg i pipelines.

## Sikkerhet

- Container kjører som ikke-root bruker.
- Tjenesten logger ikke hemmeligheter som standard. Håndter API-nøkler via sikre miljøvariabler eller secrets.

## Lokalt uten Docker (utvikling)

```bash
cd insert_metadata
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 9013 --reload
```

---

http://127.0.0.1:9013/docs#/
