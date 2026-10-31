"""Corre el set dorado contra la Lambda desplegada y escribe el reporte.

Para cada caso revisa cuatro cosas:
  1. Tipo de respuesta (respuesta, no_encontrado, no_verificado, bloqueado).
  2. Datos esperados: cada grupo de 'datos' debe aparecer en la respuesta.
     Esto mide COMPLETITUD, que el grounding del guardrail no mide.
  3. Página citada: al menos una cita a una página esperada (± 1).
  4. Aviso de taller: debe salir solo cuando corresponde.

Invoca la Lambda directo (con tus credenciales de AWS), sin pasar por CloudFront.

Uso (desde fase0-manuales/):
    uv run --project infra python pruebas/evaluar.py
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import boto3

REGION = "us-east-1"
PILA = "ManualesVehiculos"
CARPETA = Path(__file__).resolve().parent


def nombre_funcion() -> str:
    cfn = boto3.client("cloudformation", region_name=REGION)
    recursos = cfn.describe_stack_resources(StackName=PILA)["StackResources"]
    return next(
        r["PhysicalResourceId"]
        for r in recursos
        if r["ResourceType"] == "AWS::Lambda::Function" and r["LogicalResourceId"].startswith("Asistente")
    )


def preguntar(lam, funcion: str, caso: dict) -> dict:
    # Mismo formato de evento que envía la Function URL
    evento = {"body": json.dumps({"vehiculo_id": caso["vehiculo_id"], "pregunta": caso["pregunta"]})}
    r = lam.invoke(FunctionName=funcion, Payload=json.dumps(evento))
    return json.loads(json.loads(r["Payload"].read())["body"])


def evaluar(caso: dict, r: dict) -> dict:
    texto = (r.get("respuesta") or "").lower().replace(" ", " ")
    citadas = [c["pagina"] for c in r.get("citas", []) if c.get("pagina") is not None]
    revision = {}

    revision["tipo"] = r.get("tipo") in caso["tipo_esperado"]

    faltan = [g[0] for g in caso["datos"] if not any(v.lower() in texto for v in g)]
    revision["datos"] = not faltan

    if caso["paginas"]:
        revision["pagina"] = any(abs(c - p) <= 1 for c in citadas for p in caso["paginas"])

    if "aviso_taller" in caso:
        revision["taller"] = bool(r.get("aviso_taller")) == caso["aviso_taller"]

    return {
        "id": caso["id"],
        "trampa": caso.get("trampa", False),
        "aprobado": all(revision.values()),
        "revision": revision,
        "faltan": faltan,
        "tipo": r.get("tipo"),
        "citas": citadas,
        "respuesta": r.get("respuesta", ""),
        "verdad": caso["verdad"],
        "pregunta": caso["pregunta"],
        "vehiculo_id": caso["vehiculo_id"],
    }


def reporte(resultados: list[dict], umbral: dict) -> tuple[str, bool]:
    aprobados = sum(r["aprobado"] for r in resultados)
    trampas_ok = all(r["aprobado"] for r in resultados if r["trampa"])
    pasa = aprobados >= umbral["minimo_aprobados"] and (trampas_ok or not umbral["trampas_obligatorias"])

    marca = lambda ok: "✅" if ok else "❌"
    lineas = [
        "# Resultados del set dorado",
        "",
        f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Aprobados: {aprobados} de {len(resultados)}** · Trampas: {'todas aprobadas' if trampas_ok else 'alguna falló'} · "
        f"Umbral ({umbral['minimo_aprobados']} de {len(resultados)} y trampas obligatorias): **{'PASA' if pasa else 'NO PASA'}**",
        "",
        "| # | Caso | Tipo | Datos | Página | Taller | Resultado |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(resultados, 1):
        v = r["revision"]
        celda = lambda k: marca(v[k]) if k in v else "n/a"
        lineas.append(
            f"| {i} | {r['id']}{' (trampa)' if r['trampa'] else ''} | {celda('tipo')} | {celda('datos')} | "
            f"{celda('pagina')} | {celda('taller')} | {marca(r['aprobado'])} |"
        )
    lineas += ["", "## Detalle", ""]
    for i, r in enumerate(resultados, 1):
        lineas += [
            f"### {i}. {r['id']} {marca(r['aprobado'])}",
            "",
            f"- **Vehículo:** `{r['vehiculo_id']}`",
            f"- **Pregunta:** {r['pregunta']}",
            f"- **Verdad (verificada en el PDF):** {r['verdad']}",
            f"- **Tipo:** `{r['tipo']}` · **Páginas citadas:** {r['citas'] or 'ninguna'}",
        ]
        if r["faltan"]:
            lineas.append(f"- **Datos que faltan:** {', '.join(r['faltan'])}")
        respuesta = r["respuesta"].replace("\n", " ").strip()
        lineas += [f"- **Respuesta:** {respuesta}", ""]
    return "\n".join(lineas), pasa


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    set_dorado = json.loads((CARPETA / "set_dorado.json").read_text(encoding="utf-8"))
    lam = boto3.client("lambda", region_name=REGION)
    funcion = nombre_funcion()

    resultados = []
    for caso in set_dorado["casos"]:
        inicio = time.time()
        r = preguntar(lam, funcion, caso)
        res = evaluar(caso, r)
        resultados.append(res)
        print(f"{'OK  ' if res['aprobado'] else 'FALLA'} {caso['id']:28} {res['revision']}  ({time.time() - inicio:.1f} s)")

    texto, pasa = reporte(resultados, set_dorado["umbral"])
    (CARPETA / "resultados.md").write_text(texto, encoding="utf-8")
    # Respuestas crudas, fuera del repo (pruebas/salida/ está en .gitignore)
    (CARPETA / "salida").mkdir(exist_ok=True)
    (CARPETA / "salida" / "resultados.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'PASA' if pasa else 'NO PASA'} · reporte en pruebas/resultados.md")
    sys.exit(0 if pasa else 1)


if __name__ == "__main__":
    main()
