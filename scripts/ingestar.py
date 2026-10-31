"""Lanza la ingesta de la Knowledge Base y espera a que termine.

Ingestar = leer los PDF del bucket, partirlos en fragmentos, convertir cada
fragmento en un vector con Titan y guardarlo en S3 Vectors con sus metadatos.
Se corre después de cada `cdk deploy` que cambie los manuales (como un paso
de pipeline: desplegar la infraestructura y luego cargar los datos).

Uso (desde fase0-manuales/):
    uv run --project infra python scripts/ingestar.py
"""

import time

import boto3

PILA = "ManualesVehiculos"
REGION = "us-east-1"


def salidas_de_la_pila() -> dict:
    cfn = boto3.client("cloudformation", region_name=REGION)
    pila = cfn.describe_stacks(StackName=PILA)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in pila["Outputs"]}


def main() -> None:
    s = salidas_de_la_pila()
    agente = boto3.client("bedrock-agent", region_name=REGION)
    trabajo = agente.start_ingestion_job(
        knowledgeBaseId=s["KnowledgeBaseId"], dataSourceId=s["DataSourceId"]
    )["ingestionJob"]
    print(f"Ingesta iniciada: {trabajo['ingestionJobId']}")

    while trabajo["status"] in ("STARTING", "IN_PROGRESS"):
        time.sleep(15)
        trabajo = agente.get_ingestion_job(
            knowledgeBaseId=s["KnowledgeBaseId"],
            dataSourceId=s["DataSourceId"],
            ingestionJobId=trabajo["ingestionJobId"],
        )["ingestionJob"]
        print(f"  estado: {trabajo['status']}")

    print(f"Resultado: {trabajo['status']}")
    print(f"Estadísticas: {trabajo.get('statistics')}")
    if trabajo.get("failureReasons"):
        print(f"Fallas: {trabajo['failureReasons']}")


if __name__ == "__main__":
    main()
