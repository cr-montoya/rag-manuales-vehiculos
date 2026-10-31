"""Punto de entrada de CDK: crea la app y la única pila de la demo.

La cuenta y la región salen del entorno de la CLI (CDK_DEFAULT_ACCOUNT y
CDK_DEFAULT_REGION). La región se fija con --region o AWS_REGION al desplegar;
no se escribe la cuenta en el código porque el repo se muestra en pantalla.
"""

import aws_cdk as cdk

from infra_manuales.stack import ManualesStack

app = cdk.App()

ManualesStack(
    app,
    "ManualesVehiculos",
    env=cdk.Environment(
        account=app.node.try_get_context("cuenta") or None,
        region=app.node.try_get_context("region") or "us-east-1",
    ),
    description="Demo RAG: asistente de manuales de vehículos (Bedrock Knowledge Base + S3 Vectors)",
)

# Etiquetas en todos los recursos: sirven para ver el costo de la demo en Cost Explorer
cdk.Tags.of(app).add("proyecto", "manuales-vehiculos")
cdk.Tags.of(app).add("ambiente", "demo")

app.synth()
