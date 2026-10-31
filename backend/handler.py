"""Lambda del asistente de manuales.

Una petición pasa por cinco pasos, siempre en este orden:

  1. Vehículo: sin vehículo conocido no se responde; se pregunta cuál es.
  2. Guardrail de entrada: si la pregunta trae prompt injection, se corta aquí.
  3. Foto (opcional): Nova describe el testigo del tablero y esa descripción
     se suma a la búsqueda. La foto no reemplaza al manual; solo arma la consulta.
  4. Búsqueda filtrada: la Knowledge Base busca SOLO en los fragmentos cuyo
     metadato marca/modelo/año coincide con el vehículo elegido.
  5. Respuesta: Nova redacta con los fragmentos y cita la página.
  6. Verificación: el guardrail compara la respuesta con los fragmentos
     (grounding contextual). Si afirma algo que no está, no se entrega.
     Si la pregunta es de seguridad, el código (no el modelo) agrega "llévalo al taller".

Cada paso deja un log JSON en CloudWatch: es la evidencia de la demo.
"""

import base64
import json
import os
import re
import time

import boto3

KB_ID = os.environ["KB_ID"]
MODELO_ID = os.environ["MODELO_ID"]
GUARDRAIL_ID = os.environ["GUARDRAIL_ID"]
GUARDRAIL_VERSION = os.environ["GUARDRAIL_VERSION"]
CATALOGO = json.loads(os.environ["CATALOGO"])

# Clientes creados fuera del handler: se reutilizan entre invocaciones (arranque en caliente)
bedrock = boto3.client("bedrock-runtime")
kb = boto3.client("bedrock-agent-runtime")

NO_ENCONTRADO = "No encontré eso en el manual de tu vehículo."
NO_VERIFICADO = (
    "Encontré información relacionada en el manual, pero no pude verificar una respuesta "
    "exacta. Revisa las páginas citadas abajo o consulta a un técnico."
)
AVISO_TALLER = "Por seguridad, llévalo al taller para que lo revise un técnico."

# Política de seguridad determinista: el aviso sale cuando la PREGUNTA o la FOTO
# describen un síntoma o un sistema crítico. Vive en código porque no debe depender
# del modelo. Lección aprendida: con la palabra "aceite" sola, el aviso salía hasta
# en "¿cuánto aceite lleva?"; un aviso que sale siempre deja de servir.
TEMAS_SEGURIDAD = re.compile(
    r"fren|temperatura|sobrecalent|recalent|refrigerante|bolsa de aire|airbag|direcci[oó]n|"
    r"humo|fuga|goteo|olor a quemado|ruido|vibra|"
    r"(luz|testigo|indicador)\w*\s+(rojo|roja|encendid|parpade|de (aceite|presi[oó]n|motor|bater[ií]a|freno))|"
    r"presi[oó]n de(l)? aceite|se (apag|recalient|calient)",
    re.IGNORECASE,
)

INSTRUCCIONES = """Eres un asistente que responde preguntas sobre el manual del propietario de un vehículo.
Reglas:
- Responde SOLO con la información de los fragmentos del manual que te doy. No uses conocimiento propio.
- Después de cada dato, cita la página así: (pág. N).
- Si el dato viene de una tabla de intervalos o de valores (kilómetros, presiones, capacidades),
  incluye todos los valores relevantes de la fila: por ejemplo, el primer intervalo y la
  frecuencia de los siguientes, o el valor para cada caso (delantera y trasera, una o dos personas).
- Si los fragmentos no contienen la respuesta, responde exactamente: "{no_encontrado}"
- Máximo 4 frases, en español, claras para alguien que no sabe de mecánica.
  Si el manual está en inglés, traduce la respuesta al español y conserva la cita de página.
- No des precios, ni consejos legales, ni información de otros vehículos.""".format(no_encontrado=NO_ENCONTRADO)


def log(evento: str, **datos) -> None:
    """Log estructurado: una línea JSON por evento, fácil de consultar en Logs Insights."""
    print(json.dumps({"evento": evento, **datos}, ensure_ascii=False, default=str))


def respuesta_http(cuerpo: dict, estado: int = 200) -> dict:
    return {
        "statusCode": estado,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(cuerpo, ensure_ascii=False),
    }


# --- Paso 2: guardrail de entrada -------------------------------------------------
def revisar_entrada(texto: str) -> tuple[bool, str, list]:
    """Devuelve (bloqueado, texto_limpio, filtros_activados).

    El guardrail puede intervenir por dos motivos distintos:
    - bloquear (inyección, insultos): se corta la conversación;
    - enmascarar PII (correo, teléfono): se sigue, pero con el texto enmascarado.
    """
    r = bedrock.apply_guardrail(
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VERSION,
        source="INPUT",
        content=[{"text": {"text": texto}}],
    )
    filtros = []
    bloqueado = False
    for evaluacion in r.get("assessments", []):
        for f in evaluacion.get("contentPolicy", {}).get("filters", []):
            filtros.append(f"{f['type']}:{f['action']}")
            if f["action"] == "BLOCKED":
                bloqueado = True
        for p in evaluacion.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
            filtros.append(f"PII_{p['type']}:{p['action']}")
    salida = r.get("outputs", [])
    texto_limpio = salida[0]["text"] if salida and not bloqueado else texto
    return bloqueado, texto_limpio, filtros


# --- Paso 3: describir la foto del tablero -----------------------------------------
def describir_foto(imagen_b64: str) -> str:
    """Convierte la foto en una frase buscable, por ejemplo: 'testigo de presión de aceite'."""
    r = bedrock.converse(
        modelId=MODELO_ID,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": base64.b64decode(imagen_b64)}}},
                    {
                        "text": "Mira el tablero de este vehículo. En una sola frase corta, di qué testigo o luz "
                        "está encendida, su color y su forma (por ejemplo: 'testigo rojo con forma de aceitera'). "
                        "Si no es un tablero de vehículo, responde solo: NO_ES_TABLERO"
                    },
                ],
            }
        ],
        inferenceConfig={"maxTokens": 80, "temperature": 0},
    )
    return r["output"]["message"]["content"][0]["text"].strip()


# --- Paso 4: búsqueda filtrada por vehículo ----------------------------------------
def buscar(consulta: str, vehiculo: dict) -> list[dict]:
    """Recupera fragmentos SOLO del manual del vehículo elegido.

    El filtro se aplica antes de comparar vectores: un fragmento de otro
    vehículo nunca entra al contexto, aunque se parezca a la pregunta.
    """
    r = kb.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": consulta},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": 8,
                "filter": {
                    "andAll": [
                        {"equals": {"key": "marca", "value": vehiculo["marca"]}},
                        {"equals": {"key": "modelo", "value": vehiculo["modelo"]}},
                        {"equals": {"key": "anio", "value": vehiculo["anio"]}},
                    ]
                },
            }
        },
    )
    fragmentos = []
    for item in r.get("retrievalResults", []):
        meta = item.get("metadata", {})
        pagina = meta.get("x-amz-bedrock-kb-document-page-number")
        fragmentos.append(
            {
                "texto": item["content"]["text"],
                "pagina": int(pagina) if pagina is not None else None,
                "vehiculo": f"{meta.get('marca')} {meta.get('modelo')} {meta.get('anio')}",
                "puntaje": round(item.get("score", 0), 3),
            }
        )
    return fragmentos


# --- Paso 5: redactar la respuesta con citas ---------------------------------------
def redactar(pregunta: str, descripcion_foto: str | None, vehiculo: dict, fragmentos: list[dict]) -> tuple[str, dict, str]:
    contexto = "\n\n".join(f"[Fragmento, pág. {f['pagina']}]\n{f['texto']}" for f in fragmentos)
    texto_usuario = f"Vehículo: {vehiculo['etiqueta']}\n"
    if descripcion_foto:
        texto_usuario += f"En la foto del tablero se ve: {descripcion_foto}\n"
    texto_usuario += f"Pregunta: {pregunta}\n\nFragmentos del manual:\n{contexto}"

    # Sin guardrailConfig aquí: la salida se revisa en el paso 6, con los
    # fragmentos como fuente, para que el guardrail pueda medir el grounding.
    r = bedrock.converse(
        modelId=MODELO_ID,
        system=[{"text": INSTRUCCIONES}],
        messages=[{"role": "user", "content": [{"text": texto_usuario}]}],
        inferenceConfig={"maxTokens": 400, "temperature": 0},
    )
    texto = r["output"]["message"]["content"][0]["text"].strip()
    return texto, r.get("usage", {}), contexto


# --- Paso 6: verificar la respuesta contra los fragmentos --------------------------
def verificar_salida(pregunta: str, contexto: str, respuesta: str) -> tuple[str, dict]:
    """Devuelve (veredicto, puntajes). Veredicto: "ok", "sin_sustento" o "bloqueado".

    El guardrail recibe tres piezas, cada una marcada con su papel:
    - grounding_source: los fragmentos del manual (la verdad contra la que se compara);
    - query: la pregunta (para medir si la respuesta es relevante);
    - la respuesta del modelo (lo que se evalúa).
    """
    r = bedrock.apply_guardrail(
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VERSION,
        source="OUTPUT",
        content=[
            {"text": {"text": contexto[:100_000], "qualifiers": ["grounding_source"]}},
            {"text": {"text": pregunta[:1_000], "qualifiers": ["query"]}},
            {"text": {"text": respuesta}},
        ],
    )
    puntajes, veredicto = {}, "ok"
    for evaluacion in r.get("assessments", []):
        for f in evaluacion.get("contextualGroundingPolicy", {}).get("filters", []):
            puntajes[f["type"]] = round(f.get("score", 0), 2)
            if f.get("action") == "BLOCKED":
                veredicto = "sin_sustento"
        for f in evaluacion.get("contentPolicy", {}).get("filters", []):
            if f.get("action") == "BLOCKED":
                return "bloqueado", puntajes
    return veredicto, puntajes


def handler(event, context):
    inicio = time.time()
    try:
        cuerpo = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            cuerpo = base64.b64decode(cuerpo).decode("utf-8")
        datos = json.loads(cuerpo)
    except (ValueError, TypeError):
        return respuesta_http({"error": "JSON inválido"}, 400)

    pregunta = (datos.get("pregunta") or "").strip()[:1000]
    imagen = datos.get("imagen")  # JPEG en base64, opcional
    vehiculo_id = datos.get("vehiculo_id")

    # Paso 1: sin vehículo identificado, se pregunta antes de responder
    vehiculo = CATALOGO.get(vehiculo_id)
    if not vehiculo:
        log("sin_vehiculo", vehiculo_id=vehiculo_id)
        return respuesta_http(
            {
                "tipo": "aclaracion",
                "respuesta": "¿De qué vehículo hablamos? Elige tu moto o carro para buscar en su manual.",
                "opciones": [{"id": k, "etiqueta": v["etiqueta"]} for k, v in CATALOGO.items()],
            }
        )
    if not pregunta and not imagen:
        return respuesta_http({"error": "Escribe una pregunta o sube una foto del tablero"}, 400)

    # Paso 2: guardrail de entrada
    bloqueado, pregunta_limpia, filtros = revisar_entrada(pregunta or "¿Qué significa esta luz del tablero?")
    if bloqueado:
        log("guardrail_bloqueo", vehiculo_id=vehiculo_id, filtros=filtros)
        return respuesta_http(
            {
                "tipo": "bloqueado",
                "respuesta": "No puedo procesar esa solicitud. Pregúntame sobre el manual de tu vehículo.",
                "guardrail": filtros,
            }
        )

    # Paso 3: foto del tablero (si hay)
    descripcion_foto = None
    if imagen:
        descripcion_foto = describir_foto(imagen)
        log("foto_descrita", vehiculo_id=vehiculo_id, descripcion=descripcion_foto)
        if "NO_ES_TABLERO" in descripcion_foto:
            return respuesta_http(
                {"tipo": "aclaracion", "respuesta": "No reconozco un tablero en la foto. ¿Puedes tomarla de nuevo, de frente y con la luz encendida?"}
            )

    # Paso 4: búsqueda filtrada por vehículo
    consulta = " ".join(x for x in [pregunta_limpia, descripcion_foto] if x)
    fragmentos = buscar(consulta, vehiculo)

    # Paso 5: respuesta (sin fragmentos no se llama al modelo: no hay nada que citar)
    contexto, uso = "", {}
    if fragmentos:
        texto, uso, contexto = redactar(pregunta_limpia, descripcion_foto, vehiculo, fragmentos)
    else:
        texto = NO_ENCONTRADO

    # Si el modelo dice que no lo encontró, se responde SOLO eso: nada de relleno
    # ("visita el sitio web") que no venga del manual. Regla en código, no en el prompt.
    # Lección aprendida: el modelo a veces dice "no encontré" con otras palabras.
    # Por eso la señal es estructural: una respuesta sin ninguna cita de página
    # no tiene fuente, y se trata como "no encontrado".
    sin_citas = not re.search(r"p[áa]g\.\s*\d+", texto)
    encontrado = NO_ENCONTRADO not in texto and not sin_citas
    if not encontrado:
        texto = NO_ENCONTRADO

    # Paso 6: verificación de grounding. Una respuesta con cita real pero con un dato
    # que no está en los fragmentos no se entrega: se muestran las páginas para revisar.
    veredicto, puntajes = ("sin_revisar", {})
    if encontrado:
        veredicto, puntajes = verificar_salida(pregunta_limpia, contexto, texto)
        if veredicto == "bloqueado":
            log("guardrail_bloqueo_salida", vehiculo_id=vehiculo_id, puntajes=puntajes)
            return respuesta_http(
                {"tipo": "bloqueado", "respuesta": "No puedo responder eso. Pregúntame sobre el manual de tu vehículo."}
            )
        if veredicto == "sin_sustento":
            log("grounding_insuficiente", vehiculo_id=vehiculo_id, puntajes=puntajes, respuesta_descartada=texto)
            texto, encontrado = NO_VERIFICADO, False

    # El aviso depende de lo que PREGUNTA la persona (o muestra la foto), no de la
    # respuesta del modelo: así sale aunque no haya respuesta y no sale de más.
    seguridad = bool(TEMAS_SEGURIDAD.search(" ".join(x for x in [pregunta, descripcion_foto] if x)))
    if seguridad:
        texto = f"{texto}\n\n{AVISO_TALLER}"

    # Citas: las páginas que el modelo citó. Si la respuesta no se pudo verificar,
    # se muestran las páginas más relevantes para que la persona las revise.
    if veredicto == "sin_sustento":
        paginas_citadas = {f["pagina"] for f in fragmentos[:3]}
    else:
        paginas_citadas = {int(p) for p in re.findall(r"p[áa]g\.\s*(\d+)", texto)}
    citas = [
        {"pagina": f["pagina"], "extracto": f["texto"][:300]}
        for f in fragmentos
        if f["pagina"] in paginas_citadas
    ]
    # Una sola cita por página
    citas = list({c["pagina"]: c for c in citas}.values())

    log(
        "respuesta",
        vehiculo_id=vehiculo_id,
        con_foto=bool(imagen),
        guardrail=filtros,
        fragmentos=[{"pagina": f["pagina"], "vehiculo": f["vehiculo"], "puntaje": f["puntaje"]} for f in fragmentos],
        paginas_citadas=sorted(paginas_citadas),
        encontrado=encontrado,
        grounding=veredicto,
        grounding_puntajes=puntajes,
        aviso_taller=seguridad,
        tokens=uso,
        latencia_ms=int((time.time() - inicio) * 1000),
    )

    tipo = "respuesta" if encontrado else ("no_verificado" if veredicto == "sin_sustento" else "no_encontrado")
    return respuesta_http(
        {
            "tipo": tipo,
            "vehiculo": vehiculo["etiqueta"],
            "respuesta": texto,
            "citas": citas,
            "descripcion_foto": descripcion_foto,
            "aviso_taller": seguridad,
        }
    )
