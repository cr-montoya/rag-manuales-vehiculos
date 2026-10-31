"""Carril 2. Vector store: S3 Vectors (bucket de vectores + índice).

Alternativa descartada: OpenSearch Serverless, que cobra un mínimo mensual
aunque no se use. S3 Vectors cobra por lo almacenado y consultado.
"""

from aws_cdk import RemovalPolicy, aws_s3vectors as s3vectors
from constructs import Construct


class Vectores(Construct):
    def __init__(self, scope: Construct, construct_id: str, *, dimensiones: int) -> None:
        super().__init__(scope, construct_id)

        self.bucket = s3vectors.CfnVectorBucket(self, "Bucket")
        self.bucket.apply_removal_policy(RemovalPolicy.DESTROY)

        self.indice = s3vectors.CfnIndex(
            self,
            "Indice",
            vector_bucket_arn=self.bucket.attr_vector_bucket_arn,
            data_type="float32",
            dimension=dimensiones,  # debe coincidir con el modelo de embeddings
            distance_metric="cosine",
            # La Knowledge Base guarda el texto del fragmento en estas dos llaves.
            # Se marcan como "no filtrables" porque los metadatos filtrables tienen
            # un límite de tamaño pequeño; marca, modelo y año sí quedan filtrables.
            metadata_configuration=s3vectors.CfnIndex.MetadataConfigurationProperty(
                non_filterable_metadata_keys=["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
            ),
        )
        self.indice.apply_removal_policy(RemovalPolicy.DESTROY)
