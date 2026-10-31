"""Pila completa de la demo: RAG sobre manuales de vehículos.

Este archivo es el índice: arma los siete carriles y los conecta. Cada carril
vive en su propio archivo. El carril 7 (alertas.py) vigila a los demás.

Recorrido de una pregunta (de izquierda a derecha):

    Celular -> [6 web.py] CloudFront (SPA) -> [5 asistente.py] Lambda (Function URL)
                                    |-- [4 guardrail.py] revisa la pregunta (inyección, PII)
                                    |-- Nova 2 Lite: si hay foto, describe el testigo del tablero
                                    |-- [3 conocimiento.py] Knowledge Base: busca SOLO en el
                                    |       manual del vehículo elegido (filtro por metadatos)
                                    |       sobre [2 vectores.py] S3 Vectors, alimentada
                                    |       desde [1 fuente.py] S3 con los PDF
                                    `-- Nova 2 Lite: redacta la respuesta con cita de página

Todo tiene RemovalPolicy.DESTROY para que `cdk destroy` no deje nada cobrando.
"""

import json
from pathlib import Path

from aws_cdk import CfnOutput, Stack
from constructs import Construct

from .alertas import Alertas
from .asistente import Asistente
from .conocimiento import Conocimiento
from .fuente import Fuente
from .guardrail import Guardrail
from .vectores import Vectores
from .web import Web

# Rutas del repo, relativas a esta carpeta (infra/infra_manuales/)
RAIZ = Path(__file__).resolve().parent.parent.parent
CARPETA_PDF = RAIZ / "corpus" / "pdf"
CATALOGO = json.loads((RAIZ / "corpus" / "manuales.json").read_text(encoding="utf-8"))

# Modelos. Se cambian aquí y en ningún otro lugar.
MODELO_EMBEDDINGS = "amazon.titan-embed-text-v2:0"  # convierte texto en vectores de 1024 números
DIMENSIONES = 1024
# Generador: Nova 2 Lite (lee texto e imágenes, barato y rápido).
# Se probó escalar a Nova Pro porque Lite omitía un intervalo de una tabla. El set dorado
# mostró que Pro no lo arreglaba y además atribuyó el lubricante de la CADENA (SAE 90,
# cada 500 km) al aceite del MOTOR, donde Lite decía con honestidad "no encontré".
# Más caro y menos seguro: se volvió a Lite. Ver docs/bitacora.md, problema 23.
MODELO_GENERADOR = "amazon.nova-2-lite-v1:0"
PERFIL_INFERENCIA = f"us.{MODELO_GENERADOR}"  # reparte la carga entre regiones de EE. UU.


class ManualesStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if not CARPETA_PDF.exists():
            raise SystemExit(
                "Falta corpus/pdf. Corre primero: uv run --project infra python scripts/descargar_manuales.py"
            )

        fuente = Fuente(self, "Fuente", carpeta_pdf=CARPETA_PDF)
        vectores = Vectores(self, "Vectores", dimensiones=DIMENSIONES)
        conocimiento = Conocimiento(
            self,
            "Conocimiento",
            fuente=fuente,
            vectores=vectores,
            modelo_embeddings=MODELO_EMBEDDINGS,
            dimensiones=DIMENSIONES,
        )
        guardrail = Guardrail(self, "Guardrail")
        asistente = Asistente(
            self,
            "Asistente",
            carpeta_codigo=RAIZ / "backend",
            catalogo=CATALOGO,
            conocimiento=conocimiento,
            guardrail=guardrail,
            modelo_generador=MODELO_GENERADOR,
            perfil_inferencia=PERFIL_INFERENCIA,
        )
        web = Web(self, "Web", carpeta_web=RAIZ / "web", catalogo=CATALOGO, asistente=asistente)
        # Los IDs de Slack llegan por --context (no se escriben en el repo)
        Alertas(
            self,
            "Alertas",
            asistente=asistente,
            slack_workspace=self.node.try_get_context("slack_workspace"),
            slack_canal=self.node.try_get_context("slack_canal"),
        )

        # Salidas: lo que necesitan los scripts de ingesta y de pruebas
        CfnOutput(self, "UrlWeb", value=web.url)
        CfnOutput(self, "KnowledgeBaseId", value=conocimiento.kb.attr_knowledge_base_id)
        CfnOutput(self, "DataSourceId", value=conocimiento.fuente_datos.attr_data_source_id)
        CfnOutput(self, "LogGroup", value=asistente.logs.log_group_name)
