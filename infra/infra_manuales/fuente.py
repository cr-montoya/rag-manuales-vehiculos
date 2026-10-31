"""Carril 1. Fuente: bucket S3 con los PDF y sus metadatos (.metadata.json).

Es el "origen de verdad": la Knowledge Base solo sabe lo que hay aquí.
"""

from pathlib import Path

from aws_cdk import RemovalPolicy, aws_s3 as s3, aws_s3_deployment as s3deploy
from constructs import Construct


class Fuente(Construct):
    def __init__(self, scope: Construct, construct_id: str, *, carpeta_pdf: Path) -> None:
        super().__init__(scope, construct_id)

        # Privado, cifrado y solo HTTPS. auto_delete_objects vacía el bucket al destruir.
        self.bucket = s3.Bucket(
            self,
            "Bucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Sube corpus/pdf/ al prefijo manuales/ (en infraestructura, esto es "el artefacto")
        s3deploy.BucketDeployment(
            self,
            "SubirManuales",
            sources=[s3deploy.Source.asset(str(carpeta_pdf))],
            destination_bucket=self.bucket,
            destination_key_prefix=self.PREFIJO,
            memory_limit=1024,  # los PDF pesan varios MB; con 128 MB la copia es lenta
        )

    PREFIJO = "manuales/"
