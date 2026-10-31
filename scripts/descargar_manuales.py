"""Descarga los manuales desde el sitio oficial de cada fabricante.

Por qué un script y no los PDF en el repo: los manuales tienen derechos de autor.
Que se puedan descargar gratis no da permiso para redistribuirlos. El repo guarda
solo el catálogo (corpus/manuales.json) y este script; los PDF quedan en
corpus/pdf/, que está en .gitignore.

Además del PDF, el script escribe un archivo <pdf>.metadata.json por manual.
La Knowledge Base de Bedrock lee esos archivos y guarda tipo, marca, modelo y año
junto a cada fragmento. Con eso la búsqueda filtra por vehículo ANTES de comparar
vectores, que es el control contra "responder con el manual equivocado".

Uso (desde fase0-manuales/):
    uv run --project infra python scripts/descargar_manuales.py
"""

import json
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CATALOGO = RAIZ / "corpus" / "manuales.json"
DESTINO = RAIZ / "corpus" / "pdf"


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    manuales = json.loads(CATALOGO.read_text(encoding="utf-8"))

    for m in manuales:
        pdf = DESTINO / m["archivo"]
        if not pdf.exists():
            print(f"Descargando {m['etiqueta']} desde {m['url']}")
            urllib.request.urlretrieve(m["url"], pdf)
        else:
            print(f"Ya existe {pdf.name}, no se descarga de nuevo")

        # Formato que espera Bedrock Knowledge Bases: un JSON con la llave
        # "metadataAttributes", al lado del PDF y con el mismo nombre + .metadata.json
        metadatos = {
            "metadataAttributes": {
                "vehiculo_id": m["id"],
                "tipo": m["tipo"],
                "marca": m["marca"],
                "modelo": m["modelo"],
                "anio": m["anio"],
            }
        }
        (DESTINO / f"{m['archivo']}.metadata.json").write_text(
            json.dumps(metadatos, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"Listo: {len(manuales)} manuales con metadatos en {DESTINO}")


if __name__ == "__main__":
    main()
