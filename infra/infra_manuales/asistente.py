"""Carril 5. Asistente: la Lambda que orquesta guardrail, visión, búsqueda filtrada y respuesta.

El código de la Lambda está en backend/handler.py; aquí solo se define dónde
corre, qué variables recibe y qué permisos tiene (uno por cosa que hace).
"""

import json
from pathlib import Path

from aws_cdk import Duration, RemovalPolicy, Stack, aws_iam as iam, aws_lambda as lambda_, aws_logs as logs
from constructs import Construct

from .conocimiento import Conocimiento
from .guardrail import Guardrail


class Asistente(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        carpeta_codigo: Path,
        catalogo: list[dict],
        conocimiento: Conocimiento,
        guardrail: Guardrail,
        modelo_generador: str,
        perfil_inferencia: str,
    ) -> None:
        super().__init__(scope, construct_id)
        pila = Stack.of(self)

        self.logs = logs.LogGroup(
            self,
            "Logs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # El catálogo viaja como variable de entorno: la Lambda solo acepta vehículos conocidos
        catalogo_lambda = {
            m["id"]: {k: m[k] for k in ("etiqueta", "tipo", "marca", "modelo", "anio")}
            for m in catalogo
        }

        self.funcion = lambda_.Function(
            self,
            "Funcion",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(carpeta_codigo)),
            timeout=Duration.seconds(60),
            memory_size=512,
            log_group=self.logs,
            environment={
                "KB_ID": conocimiento.kb.attr_knowledge_base_id,
                "MODELO_ID": perfil_inferencia,
                "GUARDRAIL_ID": guardrail.guardrail.attr_guardrail_id,
                "GUARDRAIL_VERSION": guardrail.version.attr_version,
                "CATALOGO": json.dumps(catalogo_lambda, ensure_ascii=False),
            },
        )

        # Buscar en la Knowledge Base
        self.funcion.add_to_role_policy(
            iam.PolicyStatement(actions=["bedrock:Retrieve"], resources=[conocimiento.kb.attr_knowledge_base_arn])
        )
        # Llamar al modelo. El perfil "us." enruta a varias regiones de EE. UU.:
        # se permite el perfil y el modelo base en esas regiones.
        self.funcion.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{pila.region}:{pila.account}:inference-profile/{perfil_inferencia}",
                    f"arn:aws:bedrock:*::foundation-model/{modelo_generador}",
                ],
            )
        )
        # Aplicar el guardrail
        self.funcion.add_to_role_policy(
            iam.PolicyStatement(actions=["bedrock:ApplyGuardrail"], resources=[guardrail.guardrail.attr_guardrail_arn])
        )
