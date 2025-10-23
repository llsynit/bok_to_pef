# bok_to_pef – FastAPI-mikrotjeneste

prepares xhtml files and uses daisy pipeline engine to convert file to pef


## Folder structure

```

bok_to_pef/
  ├─ artifacts/
  ├─ xslt/
  ├─ app.py               # FastAPI-wrapper
  ├─ prepare_for_braille.py.py   # Din domene-modul (importeres av app.py)
  ├─ bok_to_pef.py   # Din domene-modul (importeres av app.py)
  ├─ requirements.txt     # Python-avhengigheter
  ├─ Dockerfile           # Containeroppsett
  └─ README.md            # Denne fila
```

## Kjøremodeller

### 1) Docker (standalone)

```bash
python -m venv venv         
source venv/bin/activate   
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 39013 --reload   


```

http://127.0.0.1:39013/docs#/default